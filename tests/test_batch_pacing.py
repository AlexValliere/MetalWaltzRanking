"""Observe outgoing batch timing with invented identities and a fake clock."""

import struct
import unittest

from collector.cipher import GameDes
from collector.configuration import load_masks
from collector.connection import CollectorConnection, PRIMARY_NAME_BATCH_SIZE
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
    def connection(self, deadline=None, gap=0.25, session_seconds=3600):
        clock = Clock()
        sock = RecordingSocket(clock)
        connection = CollectorConnection(sock, load_masks(), GameDes(), b"invented-login",
                                         deadline=deadline, clock=clock.monotonic, sleeper=clock.sleep,
                                         batch_request_gap_seconds=gap, session_seconds=session_seconds)
        return connection, clock, sock

    def test_primary_names_split_into_fifty_identity_requests_before_enrichment(self):
        connection, clock, sock = self.connection(gap=1.0)
        original_send = connection.send
        requests = []
        def send(opcode, payload=b""):
            original_send(opcode, payload)
            if opcode == 15:
                players = [player for player, _ in struct.iter_unpack("<IB", payload[1:])]
                requests.append((clock.value, payload[0], players))
                for player in players:
                    connection.messages.append((380, struct.pack("<BIB", 1, player, 4)
                                                + b"Invented name\0\0"))
        connection.send = send
        connection.fetch_names(list(range(1, 122)))
        self.assertEqual(PRIMARY_NAME_BATCH_SIZE, 50)
        self.assertEqual([(at, count) for at, count, _ in requests], [(1.0, 50), (2.0, 50), (3.0, 21)])
        self.assertEqual([p for _, _, players in requests for p in players], list(range(1, 122)))
        self.assertTrue(all(connection.name_resolved(p) for p in range(1, 122)))
        self.assertEqual(connection.sent, {15: 3})

    def test_duplicate_in_later_name_chunk_is_rejected_before_first_send(self):
        connection, _, sock = self.connection()
        with self.assertRaises(ValueError):
            connection.fetch_names(list(range(1, 52)) + [1])
        self.assertFalse(sock.writes)

    def test_invalid_identity_in_later_name_chunk_is_rejected_before_first_send(self):
        connection, _, sock = self.connection()
        for invalid in (0, -1, 2**32, True, "secret"):
            with self.subTest(identity=invalid), self.assertRaises(ValueError):
                connection.fetch_names(list(range(1, 52)) + [invalid])
        self.assertFalse(sock.writes)

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

    def test_one_second_gap_counts_elapsed_reply_time_and_respects_deadline(self):
        connection, clock, sock = self.connection(deadline=2.5, gap=1.0)
        connection.send(137, struct.pack("<HIB", 5, 0, 7))
        clock.value += 0.4
        connection.send(137, struct.pack("<HIB", 5, 7, 7))
        self.assertEqual([at for at, _ in sock.writes], [1.0, 2.0])
        with self.assertRaises(CollectionDeadline):
            connection.send(137, struct.pack("<HIB", 5, 14, 7))
        self.assertEqual(clock.value, 2.5)
        self.assertEqual(len(sock.writes), 2)

    def test_invalid_gap_is_rejected_before_any_request(self):
        for gap in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.connection(gap=gap)

    def test_session_deadline_clamps_gap_and_prevents_the_next_request(self):
        connection, clock, sock = self.connection(deadline=10, gap=1.0, session_seconds=0.1)
        connection.send(137, struct.pack("<HIB", 5, 0, 200))
        clock.value += 0.05
        with self.assertRaises(TimeoutError):
            connection.send(137, struct.pack("<HIB", 5, 200, 200))
        self.assertAlmostEqual(clock.value, connection.started + connection.session_seconds)
        self.assertAlmostEqual(clock.sleeps[0], 0.05)
        self.assertEqual(len(sock.writes), 1)
        self.assertEqual(connection.sent, {137: 1})

    def test_shorter_shared_deadline_still_preempts_session_during_gap(self):
        connection, clock, sock = self.connection(deadline=1.1, gap=1.0, session_seconds=10800)
        connection.send(137, struct.pack("<HIB", 5, 0, 200))
        with self.assertRaises(CollectionDeadline):
            connection.send(137, struct.pack("<HIB", 5, 200, 200))
        self.assertEqual(clock.value, 1.1)
        self.assertEqual(len(sock.writes), 1)

    def test_invalid_session_duration_is_rejected_before_any_request(self):
        for duration in (0, -1, float("nan"), float("inf"), -float("inf")):
            with self.assertRaises(ValueError):
                self.connection(session_seconds=duration)


if __name__ == "__main__":
    unittest.main()
