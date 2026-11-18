"""Exercise raw-paste flow control and recovery after unavailable or timed-out sessions."""

from collections import deque
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from support import run_cli
from webrepl_session import LoginError, WebReplSession, _RawRepl, open_session


class PastePeer:
    """Consume each WebREPL burst through a 255-byte ring, granting 128-byte credits."""

    def __init__(self, fragment_size):
        self.fragment_size = fragment_size
        self.replies = deque()
        self.received = bytearray()
        self.bursts = []
        self.dropped = 0

    def reply(self, data):
        for offset in range(0, len(data), self.fragment_size):
            self.replies.append(data[offset:offset + self.fragment_size])

    def send(self, data, *, text):
        if not text:
            raise AssertionError("REPL traffic must use WebSocket text frames.")
        if data == b"\x05A\x01":
            self.reply(b"R\x01\x80\x00\x01")
        elif data == b"\x04":
            self.reply(b"\x04")
        else:
            self.bursts.append(bytes(data))
            previous_windows = len(self.received) // 128
            self.received.extend(data[:255])
            self.dropped += max(0, len(data) - 255)
            self.reply(b"\x01" * (len(self.received) // 128 - previous_windows))

    def recv(self, *, timeout, decode):
        if not self.replies:
            raise TimeoutError("No raw-paste response available.")
        return self.replies.popleft()


class RawPasteTests(unittest.TestCase):
    def submit(self, source, fragment_size, isolated=False):
        peer = PastePeer(fragment_size)
        session = WebReplSession(peer)
        repl = _RawRepl(session)
        repl.isolated = isolated
        with session._step("regression upload"):
            repl._submit(source)
        return peer

    def test_unicode_upload_respects_esp8266_credits_and_requested_namespace(self):
        value = "温度🌡️" * 100
        source = ("value = " + repr(value)).encode("utf-8")
        for isolated in (False, True):
            with self.subTest(isolated=isolated):
                peer = self.submit(source, fragment_size=1, isolated=isolated)
                self.assertTrue(all(burst.isascii() for burst in peer.bursts))
                self.assertEqual(peer.dropped, 0)
                namespace = {"_agentkit_fs": {}}
                exec(bytes(peer.received), namespace)
                scope = namespace["_agentkit_fs"] if isolated else namespace
                other_scope = namespace if isolated else namespace["_agentkit_fs"]
                self.assertEqual(scope.get("value"), value)
                self.assertNotIn("value", other_scope)
                if not isolated:
                    expected_wire = bytes(peer.received)

        # Keep the known failure reproducible: two initial credits overflow the ring.
        peer = PastePeer(fragment_size=1)
        session = WebReplSession(peer)
        with (
            patch.object(session.stream, "limit_paste_to_one_window"),
            session._step("unrestricted upload"),
        ):
            _RawRepl(session)._submit(source)
        self.assertEqual(len(peer.bursts[0]), 256)
        self.assertGreater(peer.dropped, 0)
        self.assertNotEqual(bytes(peer.received), expected_wire)


class DebugPeer(PastePeer):
    """Keep a simulated foreground task alive across disconnected REPL clients."""

    def __init__(self):
        super().__init__(fragment_size=3)
        self.running = False
        self.interrupts = 0
        self.sources = []

    def __enter__(self):
        self.replies.clear()
        self.reply(b"Password: ")
        return self

    def __exit__(self, *args):
        self.replies.clear()

    def send(self, data, *, text=None):
        if isinstance(data, str):
            assert data == "testpass\r"
            self.reply(b"WebREPL connected\r\n>>> ")
        elif data == b"\x03":
            self.interrupts += 1
            self.running = False
        elif data == b"\x01":
            self.reply(b"raw REPL; CTRL-B to exit\r\n>")
        elif data == b"\x02":
            self.reply(b'Type "help()" for more information.\r\n>>> ')
        else:
            if data == b"\x05A\x01":
                self.received.clear()
            super().send(data, text=text)
            if data == b"\x04":
                source = bytes(self.received)
                self.sources.append(source)
                if b"while True:" in source:
                    self.running = True
                    # No EOF arrives; the desktop timeout must not imply a stop.
                else:
                    self.reply(b"42\r\n\x04\x04>")


class SessionRecoveryTests(unittest.TestCase):
    def test_unavailable_login_can_be_retried_without_interrupting_the_application(self):
        occupied = MagicMock()
        occupied.recv.side_effect = TimeoutError
        available = MagicMock()
        available.recv.side_effect = [b"Password: ", b"WebREPL connected\r\n>>> "]
        with patch("webrepl_session.connect", side_effect=[OSError, occupied, available]):
            # The network can return before the existing WebREPL client releases it.
            for _ in range(2):
                with self.assertRaises(LoginError):
                    with open_session("127.0.0.1", "testpass"):
                        self.fail("An unavailable login must not yield a session.")
            with open_session("127.0.0.1", "testpass"):
                pass
        occupied.__exit__.assert_called_once()
        occupied.send.assert_not_called()
        available.send.assert_called_once_with("testpass\r")
        available.__exit__.assert_called_once()

    def test_probe_timeout_requires_stop_before_a_fresh_probe(self):
        peer = DebugPeer()
        with tempfile.TemporaryDirectory() as directory:
            probe = Path(directory) / "probe.py"
            probe.write_text("while True:\n    pass\n", encoding="utf-8")
            target = ["--address", "127.0.0.1", "--password-stdin"]
            with patch("webrepl_session.connect", return_value=peer):
                failed = run_cli(["run", *target, "--timeout", "0.01", str(probe)], open_session)
                self.assertEqual(failed.returncode, 5, failed.stderr)
                self.assertEqual(failed.stdout, "")
                self.assertTrue(peer.running)
                self.assertIn("stop", failed.stderr)

                stopped = run_cli(["stop", *target], open_session)
                self.assertEqual(stopped.returncode, 0, stopped.stderr)
                self.assertTrue(json.loads(stopped.stdout)["repl_ready"])
                self.assertFalse(peer.running)

                probe.write_text("print(6 * 7)\n", encoding="utf-8")
                retried = run_cli(["run", *target, str(probe)], open_session)
                self.assertEqual(retried.returncode, 0, retried.stderr)
                self.assertEqual(retried.stdout, "42\r\n")
                self.assertEqual(peer.sources, [b"while True:\n    pass\n", b"print(6 * 7)\n"])
                self.assertEqual(peer.interrupts, 3)
