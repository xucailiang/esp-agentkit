"""Exercise raw-paste flow control with the ESP8266 input-buffer limit."""

from collections import deque
import errno
import hashlib
import unittest
from unittest.mock import patch

import support  # Add the Skill scripts to the import path.
from mpremote.transport import TransportExecError
from webrepl_session import BoardError, WebReplSession, _RawRepl


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

    def test_cross_window_source_arrives_intact(self):
        for size in (127, 128, 255, 256, 1025):
            for fragment_size in (1, 64):
                with self.subTest(size=size, fragment_size=fragment_size):
                    source = b"#" + b"x" * (size - 1)
                    peer = self.submit(source, fragment_size)
                    self.assertEqual(bytes(peer.received), source)
                    self.assertEqual(peer.dropped, 0)

    def test_source_survives_windows_and_uses_the_requested_namespace(self):
        for value in ("temperature" * 100, "\u6e29\u5ea6\U0001f321\ufe0f" * 100):
            source = ("value = " + repr(value)).encode("utf-8")
            for isolated in (False, True):
                with self.subTest(ascii=source.isascii(), isolated=isolated):
                    peer = self.submit(source, fragment_size=1, isolated=isolated)
                    self.assertTrue(all(burst.isascii() for burst in peer.bursts))
                    self.assertEqual(peer.dropped, 0)
                    namespace = {"_agentkit_fs": {}}
                    exec(bytes(peer.received), namespace)
                    scope = namespace["_agentkit_fs"] if isolated else namespace
                    other_scope = namespace if isolated else namespace["_agentkit_fs"]
                    self.assertEqual(scope.get("value"), value)
                    self.assertNotIn("value", other_scope)

    def test_removing_initial_credit_limit_reproduces_data_loss(self):
        source = b"#" + b"x" * 1024
        peer = PastePeer(fragment_size=64)
        session = WebReplSession(peer)
        with (
            patch.object(session.stream, "limit_paste_to_one_window"),
            session._step("unrestricted upload"),
        ):
            _RawRepl(session)._submit(source)
        self.assertEqual(len(peer.bursts[0]), 256)
        self.assertGreater(peer.dropped, 0)
        self.assertNotEqual(hashlib.sha256(peer.received).digest(), hashlib.sha256(source).digest())

    def test_filesystem_enospc_reports_recovery_action(self):
        errors = (
            OSError(errno.ENOSPC, "full"),
            TransportExecError(1, "Traceback (most recent call last):\nOSError: 28"),
            TransportExecError(1, "Traceback (most recent call last):\nOSError: [Errno 28] ENOSPC"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__, detail=str(error)):
                session = WebReplSession(None)
                with (
                    patch.object(session, "_enter_raw"),
                    patch.object(session, "_leave_raw"),
                    patch.object(_RawRepl, "exec"),
                    self.assertRaisesRegex(BoardError, "Board storage is full.*retry"),
                ):
                    with session.filesystem():
                        raise error
