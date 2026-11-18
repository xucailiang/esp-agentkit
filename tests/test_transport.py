"""Exercise raw-paste flow control with the ESP8266 input-buffer limit."""

from collections import deque
import unittest
from unittest.mock import patch

import support  # Add the Skill scripts to the import path.
from webrepl_session import WebReplSession, _RawRepl


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
