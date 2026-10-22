import socket
import struct
import unittest
from collections import Counter

from collector.cipher import GameDes
from collector.configuration import load_masks
from collector.connection import CollectorConnection, LIMITS, SESSION_SECONDS
from collector.records import login_payload, ranking_records, social_records


def primary_name(player, name):
    return 380, struct.pack("<BIB", 1, player, 4) + name.encode() + b"\0\0"


def primary_failure(*players):
    return 14, bytes([len(players)]) + b"".join(struct.pack("<I", p) for p in players)


def fallback_name(player, name):
    return 17, struct.pack("<HI", 0, player) + name.encode() + b"\0\0"


class Clock:
    value = 1.0

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class SyntheticSocket:
    def __init__(self, clock):
        self.clock, self.writes = clock, []

    def sendall(self, data):
        self.writes.append(data)

    def recv(self, size):
        self.clock.value += 13
        raise socket.timeout()


class ScriptedConnection(CollectorConnection):
    def __init__(self, primary=(), fallback=(), fallback_count=0, expire=False):
        self.fake_clock = Clock()
        super().__init__(SyntheticSocket(self.fake_clock), load_masks(), GameDes(),
                         login_payload("invented-account", "invented-password"),
                         clock=self.fake_clock.monotonic, sleeper=self.fake_clock.sleep)
        self.primary, self.fallback, self.fallback_count = primary, fallback, fallback_count
        self.expire = expire

    def send(self, opcode, payload=b""):
        super().send(opcode, payload)
        if opcode == 15:
            self.messages.extend(self.primary)
            if self.expire:
                self.fake_clock.value += SESSION_SECONDS
        elif opcode == 16 and self.sent[16] == self.fallback_count:
            self.messages.extend(self.fallback)


class ConnectionTests(unittest.TestCase):
    def test_only_reviewed_read_opcodes_exist(self):
        self.assertEqual(set(LIMITS), {324, 396, 316, 357, 374, 436, 137, 15, 16, 65531})
        connection = ScriptedConnection()
        for opcode in (141, 313, 133, 20, 238, 415, 446, 479, 488, 498):
            with self.assertRaises(ValueError):
                connection.send(opcode)
        self.assertFalse(connection.socket.writes)

    def test_session_payload_identity_guards(self):
        connection = ScriptedConnection()
        for opcode, payload in ((324, b"wrong"), (316, struct.pack("<I", 1)), (396, b"x"),
                                (137, struct.pack("<HIB", 6, 0, 200)),
                                (137, struct.pack("<HIB", 5, 0, 201)),
                                (15, struct.pack("<BIB", 1, 1, 5)), (16, struct.pack("<I", 1))):
            with self.assertRaises((ValueError, struct.error)):
                connection.send(opcode, payload)
        connection.send(324, connection.expected_login)
        with self.assertRaises(ValueError):
            connection.send(324, connection.expected_login)

    def test_mask4_completes_without_optional_online_bit(self):
        connection = ScriptedConnection(primary=(primary_name(1, "Invented 名"), primary_name(2, "Invented B")))
        connection.fetch_names([1, 2])
        self.assertEqual(connection.sent, Counter({15: 1}))
        self.assertTrue(all(connection.name_resolved(p) for p in (1, 2)))

    def test_split_masks_unicode_and_failure(self):
        connection = ScriptedConnection()
        connection.name_masks = {1: 0, 2: 0}
        connection.observe(380, struct.pack("<BIBB", 1, 1, 1, 0))
        connection.observe(*primary_name(1, "Invented 名"))
        connection.observe(*primary_failure(2))
        self.assertEqual(connection.name_masks, {1: 5, 2: 0})
        self.assertEqual(connection.name_failures, {2})

    def test_fallback_mixed_out_of_order_success_and_failure(self):
        connection = ScriptedConnection(primary=(primary_failure(1, 2, 3),),
            fallback=(fallback_name(3, "Invented C"), (17, struct.pack("<H", 48)),
                      fallback_name(1, "Invented A")), fallback_count=3)
        connection.fetch_names([1, 2, 3])
        self.assertEqual(connection.fallback_failures, {2})
        self.assertEqual(connection.fallback_status_counts, {48: 1})
        self.assertEqual(connection.received[17], 3)
        self.assertFalse(connection.name_resolved(2))

    def test_primary_timeout_recovers_once_observing_late_name(self):
        connection = ScriptedConnection(primary=(primary_name(1, "Invented A"), primary_failure(2)),
            fallback=(primary_name(2, "Late primary"), primary_failure(3),
                      fallback_name(3, "Invented C"), (17, struct.pack("<H", 48))), fallback_count=2)
        connection.fetch_names([1, 2, 3])
        connection.send(137, struct.pack("<HIB", 5, 200, 200))
        self.assertEqual(connection.primary_name_timeouts, 1)
        self.assertEqual(connection.sent, Counter({15: 1, 16: 2, 137: 1}))
        self.assertEqual(connection.names[2], "Late primary")
        self.assertTrue(connection.name_resolved(2))
        self.assertEqual(connection.fallback_failures, {2})

    def test_fallback_is_not_repeated(self):
        connection = ScriptedConnection(primary=(primary_failure(1, 2),),
            fallback=(fallback_name(2, "Invented B"),), fallback_count=1)
        connection.name_masks[1] = 0
        connection.fallback_sent.add(1)
        connection.fetch_names([1, 2])
        self.assertEqual(connection.sent, {15: 1, 16: 1})

    def test_invalid_or_incomplete_fallback_stops(self):
        cases = [(fallback_name(1, "Invented A"),),
                 (fallback_name(1, "Invented A"), fallback_name(1, "Duplicate")),
                 (fallback_name(9, "Unexpected"), fallback_name(1, "Invented A")),
                 ((380, b"\1"), fallback_name(1, "Invented A")),
                 ((131, b""), fallback_name(1, "Invented A"))]
        for fallback in cases:
            connection = ScriptedConnection(primary=(primary_failure(1, 2),), fallback=fallback, fallback_count=2)
            with self.assertRaises((TimeoutError, ValueError)):
                connection.fetch_names([1, 2])
            self.assertEqual(connection.sent, {15: 1, 16: 2})

    def test_primary_layout_failure_never_recovers(self):
        for primary in (((380, b"\1"),), ((131, b""),), ((17, b"\0\0"),)):
            connection = ScriptedConnection(primary=primary)
            with self.assertRaises(ValueError):
                connection.fetch_names([1])
            self.assertEqual(connection.sent, {15: 1})

    def test_expired_session_never_recovers(self):
        connection = ScriptedConnection(expire=True)
        with self.assertRaises(TimeoutError):
            connection.fetch_names([1])
        self.assertEqual(connection.sent, {15: 1})

    def test_out_of_order_base_is_retained(self):
        connection = ScriptedConnection()
        connection.messages.extend(((362, b"invented-base"), (312, b"invented-selection")))
        self.assertEqual(connection.receive(312), b"invented-selection")
        self.assertEqual(connection.receive(362), b"invented-base")

    def test_heartbeat_only_verified_ack(self):
        connection = ScriptedConnection()
        connection.messages.extend(((65530, b""), (65532, b"unknown"), (131, b"target")))
        self.assertEqual(connection.receive(131), b"target")
        self.assertEqual(connection.sent, {65531: 1})

    def test_ranking_count_boundary_and_trailer(self):
        self.assertEqual(ranking_records(struct.pack("<HBIiI", 0, 1, 12, 99, 0xffffffff), 200), (0, [(12, 99)]))
        self.assertEqual(ranking_records(struct.pack("<HBI", 0, 0, 17), 200), (0, []))
        self.assertEqual(ranking_records(struct.pack("<H", 7), 200), (7, []))
        for body in (struct.pack("<HBIiI", 0, 1, 0, 99, 0), struct.pack("<HBIiI", 0, 1, 12, 99, 0) + b"x"):
            with self.assertRaises(ValueError):
                ranking_records(body, 200)
        with self.assertRaises(ValueError):
            ranking_records(struct.pack("<HBIiI", 0, 1, 12, 99, 0), 0)


if __name__ == "__main__":
    unittest.main()
