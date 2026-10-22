"""Observe outgoing batch timing with invented identities and a fake clock."""

import struct
import unittest

from collector.cipher import GameDes
from collector.configuration import load_masks
from collector.connection import CollectorConnection
from collector.timing import CollectionDeadline


class Clock:
    def __init__(self):
        self.value, self.sleeps = 1.0, []

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


class RecordingSocket:
    def __init__(self, clock):
        self.clock, self.writes = clock, []

    def sendall(self, wire):
        self.writes.append((self.clock.value, wire))


class BatchPacingTests(unittest.TestCase):
    def connection(self, deadline=None):
        clock = Clock()
        sock = RecordingSocket(clock)
        connection = CollectorConnection(sock, load_masks(), GameDes(), b"invented-login",
                                         deadline=deadline, clock=clock.monotonic, sleeper=clock.sleep)
        return connection, clock, sock

    def test_mixed_ranking_name_and_vehicle_batches_share_250ms_gap(self):
        connection, clock, sock = self.connection()
        players = range(1, 201)
        connection.name_masks = dict.fromkeys(players, 0)
        connection.vehicle_pending = set(range(1, 51))
        connection.send(137, struct.pack("<HIB", 5, 0, 200))
        clock.value += 0.05
        connection.send(15, bytes([200]) + b"".join(struct.pack("<IB", p, 5) for p in players))
        clock.value += 0.08
        connection.send(20, bytes([50]) + b"".join(struct.pack("<IB", p, 12) for p in range(1, 51)))
        self.assertEqual([at for at, _ in sock.writes], [1.0, 1.25, 1.5])
        self.assertEqual(connection.sent, {137: 1, 15: 1, 20: 1})

    def test_elapsed_reply_time_does_not_add_an_unnecessary_pause(self):
        connection, clock, sock = self.connection()
        connection.send(137, struct.pack("<HIB", 5, 0, 200))
        clock.value += 0.4
        connection.send(137, struct.pack("<HIB", 5, 200, 200))
        self.assertEqual(clock.sleeps, [])
        self.assertEqual([at for at, _ in sock.writes], [1.0, 1.4])

    def test_heartbeat_ack_is_immediate_and_does_not_reset_batch_spacing(self):
        connection, clock, sock = self.connection()
        connection.send(137, struct.pack("<HIB", 5, 0, 200))
        clock.value += 0.05
        connection.send(65531)
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(sock.writes[-1][0], 1.05)
        connection.send(137, struct.pack("<HIB", 5, 200, 200))
        self.assertEqual(sock.writes[-1][0], 1.25)

    def test_deadline_during_gap_prevents_the_next_request(self):
        connection, clock, sock = self.connection(deadline=1.1)
        connection.send(137, struct.pack("<HIB", 5, 0, 200))
        clock.value += 0.05
        with self.assertRaises(CollectionDeadline):
            connection.send(137, struct.pack("<HIB", 5, 200, 200))
        self.assertEqual(clock.value, 1.1)
        self.assertEqual(len(sock.writes), 1)
        self.assertEqual(connection.sent, {137: 1})


if __name__ == "__main__":
    unittest.main()
