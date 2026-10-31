"""Sequential nickname reads share pacing across calls and other requests."""
import socket
import struct
import unittest

from collector.cipher import GameDes
from collector.configuration import load_masks
from collector.connection import CollectorConnection, NICKNAME_BATCH_SIZE, SESSION_SECONDS
from collector.timing import CollectionDeadline


class Clock:
    def __init__(self):
        self.value, self.sleeps = 1.0, []

    def monotonic(self):
        return self.value

    def sleep(self, duration):
        self.sleeps.append(duration)
        self.value += duration


class Socket:
    def __init__(self, clock):
        self.clock, self.writes = clock, []

    def sendall(self, data):
        self.writes.append(data)

    def recv(self, size):
        self.clock.value += 31
        raise socket.timeout()


class PacedConnection(CollectorConnection):
    def __init__(self, *, failures=(), missing=(), wrong_identity=None, deadline=None):
        self.fake_clock = Clock()
        sock = Socket(self.fake_clock)
        super().__init__(sock, load_masks(), GameDes(), b"invented-login", deadline=deadline,
                         clock=self.fake_clock.monotonic, sleeper=self.fake_clock.sleep)
        self.name_masks = {p: 0 for p in range(1, 201)}
        self.failure_ids, self.missing_ids, self.wrong_identity = set(failures), set(missing), wrong_identity
        self.outstanding = self.maximum_outstanding = 0
        self.events, self.failures_at_next_burst = [], []

    def send(self, opcode, payload=b""):
        super().send(opcode, payload)
        if opcode != 16:
            return
        player, = struct.unpack("<I", payload)
        if self.outstanding:
            raise AssertionError("Next nickname started before its predecessor drained")
        if player in (11, 21):
            if self.outstanding:
                raise AssertionError("Next nickname burst started before prior replies drained")
            self.failures_at_next_burst.append(set(self.fallback_failures))
        self.outstanding += 1
        self.maximum_outstanding = max(self.maximum_outstanding, self.outstanding)
        self.events.append(("send", player, self.clock()))
        if player in self.missing_ids:
            return
        if player in self.failure_ids:
            response = struct.pack("<H", 48)
        else:
            returned = self.wrong_identity if player == 1 and self.wrong_identity else player
            response = struct.pack("<HI", 0, returned) + b"Invented commander\0\0"
        self.messages.append((17, response))

    def receive(self, target=None, *args, **kwargs):
        result = super().receive(target, *args, **kwargs)
        if target == 17:
            self.outstanding -= 1
            self.events.append(("receive", None, self.clock()))
        return result


class NicknamePacingTests(unittest.TestCase):
    def test_nicknames_are_serial_and_share_one_second_start_spacing(self):
        connection = PacedConnection()
        connection.fetch_nickname_fallback(list(range(1, 26)))
        self.assertEqual(NICKNAME_BATCH_SIZE, 1)
        self.assertEqual(connection.maximum_outstanding, 1)
        self.assertEqual(connection.outstanding, 0)
        self.assertEqual(connection.received[17], 25)
        self.assertEqual(connection.fake_clock.sleeps, [1.0] * 24)
        for player in range(2, 26):
            at = next(i for i, e in enumerate(connection.events) if e[:2] == ("send", player))
            prior = connection.events[at - 1]
            self.assertEqual(prior[0], "receive")
            self.assertGreaterEqual(connection.events[at][2] - prior[2], 1.0)

    def test_separate_calls_cannot_reset_the_pacing_clock(self):
        connection = PacedConnection()
        connection.fetch_nickname_fallback(list(range(1, 11)))
        connection.fetch_nickname_fallback(list(range(11, 21)))
        starts = [at for action, _, at in connection.events if action == "send"]
        self.assertEqual(starts, list(range(1, 21)))
        self.assertEqual(connection.maximum_outstanding, 1)
        self.assertEqual(connection.fake_clock.sleeps, [1.0] * 19)

    def test_nickname_and_ranking_requests_use_the_same_clock(self):
        connection = PacedConnection()
        connection.send(137, struct.pack("<HIB", 5, 0, 200))
        connection.fetch_nickname_fallback([1])
        connection.send(137, struct.pack("<HIB", 5, 200, 200))
        self.assertEqual(connection.events[0], ("send", 1, 2.0))
        self.assertEqual(connection.fake_clock.value, 3.0)
        self.assertEqual(connection.fake_clock.sleeps, [1.0, 1.0])

    def test_elapsed_response_time_counts_toward_the_next_call_gap(self):
        connection = PacedConnection()
        connection.fetch_nickname_fallback([1])
        connection.fake_clock.value += 0.75
        connection.fetch_nickname_fallback([2])
        self.assertEqual(connection.fake_clock.sleeps, [0.25])

    def test_failure_elimination_is_committed_per_complete_group(self):
        connection = PacedConnection(failures=(3, 13, 24))
        connection.fetch_nickname_fallback(list(range(1, 26)))
        self.assertEqual(connection.fallback_failures, {3, 13, 24})
        self.assertEqual(connection.failures_at_next_burst, [{3}, {3, 13}])
        self.assertEqual(connection.fallback_status_counts, {48: 3})
        self.assertTrue(all(connection.name_resolved(p) for p in set(range(1, 26)) - {3, 13, 24}))

    def test_incomplete_group_never_sends_next_burst_or_infers_failures(self):
        connection = PacedConnection(missing=(10,))
        with self.assertRaises(TimeoutError):
            connection.fetch_nickname_fallback(list(range(1, 26)))
        self.assertEqual(connection.sent[16], 10)
        self.assertEqual(connection.received[17], 9)
        self.assertEqual(connection.fallback_failures, set())
        self.assertEqual(connection.fake_clock.sleeps, [1.0] * 9)

    def test_identity_from_later_group_is_rejected_without_advancing(self):
        connection = PacedConnection(wrong_identity=20)
        with self.assertRaises(ValueError):
            connection.fetch_nickname_fallback(list(range(1, 26)))
        self.assertEqual(connection.sent[16], 1)
        self.assertEqual(connection.fake_clock.sleeps, [])

    def test_shared_deadline_interrupts_gap_before_another_burst(self):
        connection = PacedConnection(deadline=1.1)
        with self.assertRaises(CollectionDeadline):
            connection.fetch_nickname_fallback(list(range(1, 26)))
        self.assertEqual(connection.sent[16], 1)
        self.assertEqual(connection.received[17], 1)
        self.assertEqual(connection.fake_clock.value, 1.1)
        self.assertAlmostEqual(connection.fake_clock.sleeps[0], 0.1)

    def test_session_deadline_interrupts_gap_before_another_burst(self):
        connection = PacedConnection()
        connection.fake_clock.value = connection.started + SESSION_SECONDS - 0.1
        with self.assertRaises(TimeoutError):
            connection.fetch_nickname_fallback(list(range(1, 26)))
        self.assertEqual(connection.sent[16], 1)
        self.assertEqual(connection.received[17], 1)
        self.assertAlmostEqual(connection.fake_clock.sleeps[0], 0.1)

    def test_once_only_guard_rejects_whole_repeated_input_before_new_reads(self):
        connection = PacedConnection()
        connection.fetch_nickname_fallback([1])
        with self.assertRaises(ValueError):
            connection.fetch_nickname_fallback([2, 1])
        self.assertEqual(connection.sent[16], 1)
        self.assertNotIn(2, connection.fallback_sent)

    def test_session_expiring_during_gap_setup_cannot_produce_negative_sleep(self):
        connection = PacedConnection()
        original_check = connection.check_deadlines

        def expire_after_check():
            original_check()
            if connection.received[17] == 1:
                connection.fake_clock.value = connection.started + SESSION_SECONDS + 0.001

        connection.check_deadlines = expire_after_check
        with self.assertRaises(TimeoutError):
            connection.fetch_nickname_fallback(list(range(1, 26)))
        self.assertEqual(connection.sent[16], 1)
        self.assertTrue(all(duration >= 0 for duration in connection.fake_clock.sleeps))


if __name__ == "__main__":
    unittest.main()
