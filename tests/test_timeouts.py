"""Required replies get 30s; primary nickname recovery still starts at 12s."""

import socket
import struct
import unittest

from collector.cipher import GameDes
from collector.configuration import load_masks
from collector.connection import CollectorConnection, PRIMARY_NAME_SECONDS, REPLY_SECONDS
from collector.records import login_payload
from collector.transport import TransportEncoder


class Clock:
    value = 1.0

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class TimedSocket:
    def __init__(self, clock, reply_at=None, wire=b""):
        self.clock, self.reply_at, self.wire = clock, reply_at, wire

    def sendall(self, data):
        pass

    def recv(self, size):
        self.clock.value += 0.25
        if self.reply_at is not None and self.clock.value >= self.reply_at:
            wire, self.wire, self.reply_at = self.wire, b"", None
            return wire
        raise socket.timeout()


class TimeoutTests(unittest.TestCase):
    def connection(self, reply_after=None):
        clock, masks, cipher = Clock(), load_masks(), GameDes()
        encoder = TransportEncoder(masks["g2c"], cipher.encrypt)
        wire = encoder.encode(331, b"invented required reply")
        sock = TimedSocket(clock, clock.value + reply_after if reply_after is not None else None, wire)
        return CollectorConnection(sock, masks, cipher, login_payload("invented", "invented"),
                                   deadline=clock.value + 150, clock=clock.monotonic, sleeper=clock.sleep), clock

    def test_delayed_required_reply_after_12s_succeeds_within_30s(self):
        self.assertEqual(REPLY_SECONDS, 30)
        connection, clock = self.connection(reply_after=13)
        started = clock.value
        self.assertEqual(connection.receive(331), b"invented required reply")
        self.assertGreater(clock.value - started, 12)
        self.assertLess(clock.value - started, 30)

    def test_required_reply_still_stops_at_30s_without_retry(self):
        connection, clock = self.connection(reply_after=31)
        started = clock.value
        with self.assertRaises(TimeoutError):
            connection.receive(331)
        self.assertEqual(clock.value - started, 30)
        self.assertEqual(connection.sent, {})

    def test_primary_fallback_starts_at_12s_despite_30s_required_wait(self):
        self.assertEqual(PRIMARY_NAME_SECONDS, 12)
        connection, clock = self.connection()
        timestamps, original_send = {}, connection.send
        def send(opcode, payload=b""):
            original_send(opcode, payload)
            timestamps[opcode] = clock.value
            if opcode == 16:
                connection.messages.append((17, struct.pack("<HI", 0, 1) + b"Invented name\0\0"))
        connection.send = send
        connection.fetch_names([1])
        self.assertEqual(timestamps[16] - timestamps[15], 12)
        self.assertEqual(connection.primary_name_timeouts, 1)
        self.assertEqual(connection.sent, {15: 1, 16: 1})
        self.assertTrue(connection.name_resolved(1))


if __name__ == "__main__":
    unittest.main()
