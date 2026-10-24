"""Native receive-idle timers against encrypted invented peers; no network calls."""

from collections import deque
import errno
import socket
import struct
import unittest
from unittest.mock import patch

from collector.cipher import GameDes
from collector.collect import error_code, scan_server
from collector.configuration import load_masks, load_servers
from collector.connection import CollectorConnection, LIMITS
from collector.timing import CollectionDeadline
from collector.transport import TransportDecoder, TransportEncoder
from test_batch_pacing import Clock
from test_session import InventedGameSocket


class IdleSocket:
    def __init__(self, clock, masks, incoming=(), send_error=None):
        self.clock, self.incoming = clock, deque(incoming)
        self.send_error, self.outgoing, self.reads = send_error, [], 0
        self.decoder = TransportDecoder(masks["c2g"], GameDes().decrypt)

    def sendall(self, data):
        if self.send_error is not None:
            raise self.send_error
        self.outgoing.extend((self.clock.value, opcode, payload)
                             for _, opcode, payload in self.decoder.feed(data))

    def recv(self, size):
        self.reads += 1
        delay, result = self.incoming.popleft() if self.incoming else (0.25, socket.timeout())
        self.clock.value += delay
        if isinstance(result, Exception):
            raise result
        return result


class KeepaliveTests(unittest.TestCase):
    def connection(self, *, incoming=(), enabled=True, deadline=None, session_seconds=3600,
                   send_error=None):
        clock, masks = Clock(), load_masks()
        peer = IdleSocket(clock, masks, incoming, send_error)
        connection = CollectorConnection(peer, masks, GameDes(), b"invented-login",
            native_keepalive=enabled, clock=clock.monotonic, sleeper=clock.sleep,
            deadline=deadline, session_seconds=session_seconds)
        return connection, clock, peer

    @staticmethod
    def wire(*packets):
        encoder = TransportEncoder(load_masks()["g2c"], GameDes().encrypt)
        return b"".join(encoder.encode(*packet) for packet in packets)

    def test_exact_idle_stages_send_empty_pings_and_terminal_stage_stays_terminal(self):
        connection, clock, peer = self.connection()
        for elapsed, expected in ((29.999, 0), (30, 1), (39.999, 1), (40, 2), (49.999, 2), (50, 3)):
            clock.value = connection.started + elapsed
            connection._service_keepalive()
            self.assertEqual(len(peer.outgoing), expected)
        self.assertEqual([(at - connection.started, opcode, payload) for at, opcode, payload in peer.outgoing],
                         [(30, 65530, b""), (40, 65530, b""), (50, 65530, b"")])
        clock.value = connection.started + 60
        for _ in range(2):
            with self.assertRaises(ConnectionError) as caught:
                connection._service_keepalive()
            self.assertEqual(error_code(caught.exception), "connection_failed")
            self.assertEqual(connection.failure_diagnostics(caught.exception)["transportFailure"], "native_idle_timeout")
        self.assertEqual(len(peer.outgoing), 3)
        self.assertEqual(connection.idle_pings_sent, 3)

    def test_large_clock_jump_advances_only_one_stage_per_routine_call(self):
        connection, clock, peer = self.connection()
        clock.value = connection.started + 70
        for expected in (1, 2, 3):
            connection._service_keepalive()
            self.assertEqual(connection.keepalive_stage, expected)
            self.assertEqual(len(peer.outgoing), expected)
        with self.assertRaises(ConnectionError):
            connection._service_keepalive()
        self.assertEqual(len(peer.outgoing), 3)

    def test_readable_bytes_at_idle_threshold_reset_before_any_probe(self):
        connection, clock, peer = self.connection(incoming=((0, self.wire((131, b"target"))),))
        clock.value = connection.started + 30
        self.assertEqual(connection.receive(131), b"target")
        self.assertEqual(connection.keepalive_stage, 0)
        self.assertEqual(peer.outgoing, [])

    def test_any_positive_fragment_resets_timer_and_stage_before_unpacking(self):
        wire = self.wire((131, b"target"))
        connection, _, peer = self.connection(incoming=(
            (30, socket.timeout()), (9, wire[:1]), (29, socket.timeout()), (1, wire[1:])))
        self.assertEqual(connection.receive(131, timeout=100), b"target")
        self.assertEqual([(opcode, payload) for _, opcode, payload in peer.outgoing], [(65530, b"")])
        self.assertEqual(connection.keepalive_stage, 0)
        self.assertFalse(connection.decoder.buffer)

    def test_incoming_ping_ack_is_immediate_and_incoming_ack_needs_no_response(self):
        connection, clock, peer = self.connection(incoming=((1, self.wire(
            (65530, b""), (65531, b""), (131, b"target"))),))
        clock.value = connection.started + 30
        connection._service_keepalive()
        self.assertEqual(connection.receive(131), b"target")
        self.assertEqual([(at - connection.started, op, body) for at, op, body in peer.outgoing],
                         [(30, 65530, b""), (31, 65531, b"")])
        self.assertEqual(connection.keepalive_stage, 0)
        self.assertEqual(connection.heartbeat_acks_sent, 1)
        self.assertEqual(connection.idle_pings_sent, 1)

    def test_default_reply_deadline_wins_over_first_idle_threshold(self):
        connection, clock, peer = self.connection()
        with self.assertRaises(TimeoutError):
            connection.receive(131)
        self.assertEqual(clock.value - connection.started, 30)
        self.assertEqual(peer.outgoing, [])

    def test_short_name_wait_and_ack_traffic_never_extend_required_reply_deadline(self):
        connection, clock, peer = self.connection(incoming=((1, self.wire((65531, b""))),))
        clock.value = connection.started + 30
        connection._service_keepalive()
        started = clock.value
        with self.assertRaises(TimeoutError):
            connection.receive(131, timeout=12)
        self.assertEqual(clock.value - started, 12)
        self.assertEqual(connection.received[65531], 1)
        self.assertEqual(len(peer.outgoing), 1)
        self.assertEqual(connection.keepalive_stage, 0)

    def test_shared_and_session_deadlines_win_before_any_ping(self):
        for kwargs, exception in (({"deadline": 31}, CollectionDeadline),
                                  ({"session_seconds": 30}, TimeoutError)):
            connection, _, peer = self.connection(**kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(exception):
                connection.receive(131, timeout=100)
            self.assertEqual(peer.outgoing, [])

    def test_native_idle_end_during_primary_names_does_not_trigger_fallback(self):
        connection, clock, peer = self.connection()
        connection.keepalive_stage = 3
        clock.value = connection.started + 59
        with self.assertRaises(ConnectionError) as caught:
            connection.fetch_names([1])
        self.assertEqual(connection.failure_diagnostics(caught.exception)["transportFailure"], "native_idle_timeout")
        self.assertEqual([opcode for _, opcode, _ in peer.outgoing], [15])
        self.assertEqual(connection.primary_name_timeouts, 0)
        self.assertEqual(connection.fallback_sent, set())

    def test_invalid_outbound_ping_and_invalid_inbound_framing_fail_closed(self):
        connection, clock, peer = self.connection(incoming=((0, b"\0\0"),))
        with self.assertRaises(ValueError):
            connection.send(65530, b"not-empty")
        self.assertEqual(peer.outgoing, [])
        self.assertEqual(connection.sent[65530], 0)
        clock.value = connection.started + 30
        with self.assertRaises(ValueError):
            connection.receive(131)
        self.assertEqual(peer.outgoing, [])
        self.assertEqual(connection.keepalive_stage, 0)

    def test_ping_send_failure_does_not_claim_success(self):
        connection, clock, peer = self.connection(send_error=BrokenPipeError(errno.EPIPE, "SECRET"))
        clock.value = connection.started + 30
        with self.assertRaises(BrokenPipeError) as caught:
            connection._service_keepalive()
        diagnostics = connection.failure_diagnostics(caught.exception)
        self.assertEqual(diagnostics["transportFailure"], "socket_broken_pipe")
        self.assertEqual(diagnostics["idlePingsSent"], 0)
        self.assertIsNone(diagnostics["lastIdlePingAgeSeconds"])
        self.assertIsNone(diagnostics["lastSuccessfulSendAgeSeconds"])
        self.assertEqual(peer.outgoing, [])

    def test_ping_bypasses_but_does_not_reset_existing_batch_pacing(self):
        connection, clock, peer = self.connection()
        clock.value = connection.started + 30
        connection.last_request_at = clock.value - 0.05
        connection._service_keepalive()
        self.assertEqual(clock.sleeps, [])
        connection.send(137, struct.pack("<HIB", 5, 0, 200))
        self.assertEqual([op for _, op, _ in peer.outgoing], [65530, 137])
        self.assertAlmostEqual(peer.outgoing[1][0] - peer.outgoing[0][0], 0.20)

    def test_finish_drains_queued_ack_without_polling_or_proactive_probe(self):
        connection, clock, peer = self.connection()
        clock.value = connection.started + 30
        connection.messages.append((65530, b""))
        connection.finish_vehicles()
        self.assertEqual(peer.reads, 0)
        self.assertEqual([(op, body) for _, op, body in peer.outgoing], [(65531, b"")])

    def test_keepalive_requires_boolean_opt_in_and_has_bounded_per_session_quota(self):
        for value in (0, 1, None, "true"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.connection(enabled=value)
        connection, _, peer = self.connection(enabled=False)
        with self.assertRaises(ValueError):
            connection.send(65530)
        self.assertFalse(peer.outgoing)
        for duration, quota in ((3600, 1000), (19800, 5500)):
            with self.subTest(duration=duration):
                connection, _, peer = self.connection(session_seconds=duration)
                self.assertEqual(connection.request_limits, {**LIMITS, 65530: quota, 65531: quota})
                connection.sent[65530] = quota - 1
                connection.send(65530)
                with self.assertRaises(ValueError):
                    connection.send(65530)
                self.assertEqual(len(peer.outgoing), 1)
                self.assertEqual(connection.idle_pings_sent, 1)

    def test_production_enables_only_mobile_and_keeps_steam_ack_only(self):
        for key, enabled in (("Bonn", True), ("SteamBonn", False)):
            server = next(item for item in load_servers() if item["key"] == key)
            peer, clock, connections = InventedGameSocket(load_masks(server["protocolProfile"])), Clock(), []
            def connect(*args, **kwargs):
                kwargs["sleeper"] = clock.sleep
                connection = CollectorConnection(*args, **kwargs)
                connections.append(connection)
                return connection
            with self.subTest(key=key), patch("collector.collect.open_socket", return_value=peer), \
                    patch("collector.collect.CollectorConnection", side_effect=connect):
                result = scan_server(server, ("invented", "invented"), load_masks(), clock=clock.monotonic)
            self.assertEqual(len(result["rows"]), 3)
            self.assertEqual(connections[0].native_keepalive, enabled)
            self.assertEqual(connections[0].request_limits[65530] > 0, enabled)
            self.assertTrue(peer.closed)


if __name__ == "__main__":
    unittest.main()
