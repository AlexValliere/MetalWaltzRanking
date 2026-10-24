"""Failure phases are fixed strings in finished logs, never public metadata."""

import errno
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from collector.cipher import GameDes
from collector.collect import enumerate_rankings, error_code, run_collection, scan_server
from collector.configuration import load_masks, load_servers
from collector.connection import CollectorConnection
from collector.snapshots import atomic_json
from collector.timing import CollectionDeadline
from collector.transport import TransportEncoder
from test_collection import SyntheticRanking, snapshot
from test_connection import Clock, ScriptedConnection
from test_session import InventedGameSocket


class DiagnosticSocket:
    def __init__(self, clock, incoming=(), send_error=None):
        self.clock, self.incoming, self.send_error = clock, list(incoming), send_error
        self.writes = []

    def sendall(self, data):
        if self.send_error is not None:
            raise self.send_error
        self.writes.append(data)

    def recv(self, size):
        delay, result = self.incoming.pop(0) if self.incoming else (31, socket.timeout())
        self.clock.value += delay
        if isinstance(result, Exception):
            raise result
        return result


class DiagnosticTests(unittest.TestCase):
    def connection(self, *, packets=(), send_error=None):
        clock, masks = Clock(), load_masks()
        encoder = TransportEncoder(masks["g2c"], GameDes().encrypt)
        incoming = [(delay, encoder.encode(*value) if isinstance(value, tuple) else value)
                    for delay, value in packets]
        peer = DiagnosticSocket(clock, incoming, send_error)
        connection = CollectorConnection(peer, masks, GameDes(), b"invented-login",
                                         clock=clock.monotonic, sleeper=clock.sleep)
        return connection, clock, peer

    def test_exact_handshake_and_ranking_failure_phase_is_preserved(self):
        cases = [(331, "login"), (310, "commander_list"), (312, "selection"),
                 (362, "commander_base"), (368, "town_prepare"), (371, "town_entry"),
                 (438, "achievements"), (131, "rankings"), (None, "primary_names"),
                 (17, "nickname_fallback")]
        original = CollectorConnection.receive
        for target, expected in cases:
            masks = load_masks()
            peer = InventedGameSocket(masks)
            def receive(connection, actual=None, **kwargs):
                if actual == target:
                    # Layout failures propagate immediately; a primary timeout
                    # intentionally starts the separate native fallback.
                    raise ValueError("Invented unsafe packet text")
                return original(connection, actual, **kwargs)
            with self.subTest(phase=expected), patch("collector.collect.open_socket", return_value=peer), \
                    patch.object(CollectorConnection, "receive", receive):
                with self.assertRaises(ValueError) as caught:
                    scan_server(load_servers()[0], ("invented", "invented"), masks)
            self.assertEqual(caught.exception.collector_read_phase, expected)
            self.assertTrue(peer.closed)

    def test_connect_failure_is_identified_before_any_login(self):
        with patch("collector.collect.open_socket", side_effect=ConnectionError("Invented connection failure")):
            with self.assertRaises(ConnectionError) as caught:
                scan_server(load_servers()[0], ("invented", "invented"), load_masks())
        self.assertEqual(caught.exception.collector_read_phase, "connect")

    def test_heartbeat_does_not_replace_pending_read_phase(self):
        connection = ScriptedConnection()
        connection.read_phase = "nickname_fallback"
        connection.messages.extend(((65530, b""), (17, b"invented target")))
        self.assertEqual(connection.receive(17), b"invented target")
        self.assertEqual(connection.sent, {65531: 1})
        self.assertEqual(connection.read_phase, "nickname_fallback")

    def test_explicit_session_end_and_eof_have_distinct_fixed_reasons(self):
        for packet, reason in (((317, b"SECRET unparsed logout body"), "server_session_ended"),
                               (b"", "socket_eof")):
            connection, _, _ = self.connection(packets=((1, packet),))
            with self.subTest(reason=reason), self.assertRaises(ConnectionError) as caught:
                connection.receive(131)
            self.assertEqual(error_code(caught.exception), "connection_failed")
            diagnostics = connection.failure_diagnostics(caught.exception)
            self.assertEqual(diagnostics["transportFailure"], reason)
            self.assertEqual(diagnostics["transportOperation"], "receive")
            self.assertNotIn("SECRET", repr(diagnostics))

    def test_socket_read_and_write_failures_preserve_exception_and_safe_categories(self):
        cases = ((ConnectionResetError, errno.ECONNRESET, "socket_reset"),
                 (BrokenPipeError, errno.EPIPE, "socket_broken_pipe"),
                 (OSError, errno.EIO, "socket_other"))
        for operation in ("send", "receive"):
            for kind, number, reason in cases:
                error = kind(number, "SECRET packet, account and address text")
                connection, _, peer = self.connection(
                    packets=((1, error),) if operation == "receive" else (),
                    send_error=error if operation == "send" else None)
                with self.subTest(operation=operation, reason=reason), self.assertRaises(OSError) as caught:
                    if operation == "send":
                        connection.send(65531)
                    else:
                        connection.receive(131)
                self.assertIs(caught.exception, error)
                self.assertEqual(error_code(error), "connection_failed")
                diagnostics = connection.failure_diagnostics(error)
                self.assertEqual(diagnostics["transportFailure"], reason)
                self.assertEqual(diagnostics["transportOperation"], operation)
                self.assertIsNone(diagnostics["lastSuccessfulSendAgeSeconds"])
                self.assertEqual(diagnostics["heartbeatAcksSent"], 0)
                self.assertFalse(peer.writes)
                self.assertNotIn("SECRET", repr(diagnostics))

    def test_heartbeat_counts_and_ages_use_successful_io_and_any_received_bytes(self):
        connection, clock, peer = self.connection(packets=(
            (2, (65530, b"")), (3, (65532, b"ignored")), (4, (131, b"target"))))
        self.assertEqual(connection.receive(131), b"target")
        clock.value += 1
        diagnostics = connection.failure_diagnostics(TimeoutError("SECRET timeout text"))
        self.assertEqual(diagnostics, {
            "sessionElapsedSeconds": 10.0, "lastReceiveAgeSeconds": 1.0,
            "lastSuccessfulSendAgeSeconds": 8.0, "heartbeatsReceived": 1, "heartbeatAcksSent": 1,
            "lastHeartbeatReceiveAgeSeconds": 8.0, "lastHeartbeatAckAgeSeconds": 8.0,
            "idlePingsSent": 0, "lastIdlePingAgeSeconds": None,
            "attemptRowsFetched": 0, "attemptRowsCompleted": 0})
        self.assertEqual(len(peer.writes), 1)

    def test_failed_ack_write_is_not_reported_as_a_successful_ack(self):
        connection, _, peer = self.connection(packets=((1, (65530, b"")),),
            send_error=BrokenPipeError(errno.EPIPE, "SECRET write failure"))
        with self.assertRaises(BrokenPipeError) as caught:
            connection.receive(131)
        diagnostics = connection.failure_diagnostics(caught.exception)
        self.assertEqual(connection.sent[65531], 1)  # Existing reservation/attempt counter stays unchanged.
        self.assertEqual(diagnostics["heartbeatsReceived"], 1)
        self.assertEqual(diagnostics["heartbeatAcksSent"], 0)
        self.assertIsNone(diagnostics["lastSuccessfulSendAgeSeconds"])
        self.assertIsNone(diagnostics["lastHeartbeatAckAgeSeconds"])
        self.assertEqual(diagnostics["transportFailure"], "socket_broken_pipe")
        self.assertFalse(peer.writes)

    def test_queued_heartbeat_age_measures_dispatch_separately_from_socket_arrival(self):
        encoder = TransportEncoder(load_masks()["g2c"], GameDes().encrypt)
        wire = encoder.encode(131, b"target") + encoder.encode(65530, b"")
        connection, clock, _ = self.connection(packets=((2, wire),))
        self.assertEqual(connection.receive(131), b"target")
        before = connection.failure_diagnostics(ValueError())
        self.assertEqual(before["heartbeatsReceived"], 0)
        self.assertIsNone(before["lastHeartbeatReceiveAgeSeconds"])
        self.assertEqual(before["lastReceiveAgeSeconds"], 0)
        clock.value += 5
        connection._drain_queued_messages()
        after = connection.failure_diagnostics(ValueError())
        self.assertEqual(after["heartbeatsReceived"], 1)
        self.assertEqual(after["heartbeatAcksSent"], 1)
        self.assertEqual(after["lastHeartbeatReceiveAgeSeconds"], 0)
        self.assertEqual(after["lastHeartbeatAckAgeSeconds"], 0)
        self.assertEqual(after["lastReceiveAgeSeconds"], 5)

    def test_reply_session_and_collection_timeouts_are_not_socket_errors(self):
        connection, _, peer = self.connection()
        with self.assertRaises(TimeoutError) as caught:
            connection.receive(131)
        for error in (caught.exception, TimeoutError("Collector session deadline"), CollectionDeadline()):
            with self.subTest(kind=type(error).__name__):
                diagnostics = connection.failure_diagnostics(error)
                self.assertNotIn("transportFailure", diagnostics)
                self.assertNotIn("transportOperation", diagnostics)
        self.assertFalse(peer.writes)
        self.assertEqual(connection.received[65530], 0)

    def test_arbitrary_transport_attributes_cannot_replace_the_original_error(self):
        connection, _, _ = self.connection()
        error = ValueError("SECRET original exception")
        error.collector_connection_reason = ["SECRET"]
        error.collector_connection_operation = ["SECRET"]
        self.assertNotIn("transportFailure", connection.failure_diagnostics(error))

    def test_attempt_row_counts_distinguish_fetched_page_from_completed_work(self):
        connection = SyntheticRanking(450)
        calls, progress = [], []
        def vehicles(players):
            calls.append(len(players))
            if len(calls) == 2:
                raise ConnectionResetError(errno.ECONNRESET, "SECRET peer failure")
        connection.fetch_vehicles = vehicles
        with self.assertRaises(ConnectionResetError):
            enumerate_rankings(connection, progress=progress.append)
        self.assertEqual(connection.ranking_rows_fetched, 400)
        self.assertEqual(connection.ranking_rows_completed, 200)
        self.assertEqual(progress, [])  # No fabricated 5,000-row progress checkpoint.

    def test_full_scan_attaches_current_attempt_diagnostics_before_closing_socket(self):
        class ResetDuringVehicles(InventedGameSocket):
            def recv(self, size):
                if self.sent[20]:
                    raise ConnectionResetError(errno.ECONNRESET, "SECRET raw socket failure")
                return super().recv(size)
        masks = load_masks()
        peer = ResetDuringVehicles(masks)
        with patch("collector.collect.open_socket", return_value=peer), self.assertRaises(ConnectionResetError) as caught:
            scan_server(load_servers()[0], ("invented", "invented"), masks)
        diagnostics = caught.exception.collector_diagnostics
        self.assertEqual(caught.exception.collector_read_phase, "public_vehicles")
        self.assertEqual(diagnostics["transportFailure"], "socket_reset")
        self.assertEqual(diagnostics["transportOperation"], "receive")
        self.assertEqual(diagnostics["attemptRowsFetched"], 3)
        self.assertEqual(diagnostics["attemptRowsCompleted"], 0)
        self.assertEqual(diagnostics["heartbeatsReceived"], 2)
        self.assertEqual(diagnostics["heartbeatAcksSent"], 2)
        self.assertTrue(peer.closed)

    def test_only_fixed_phase_appears_in_finished_log_and_not_manifest(self):
        server = load_servers()[0]
        for supplied, expected in (("nickname_fallback", "nickname_fallback"),
                                   ("SECRET_PHASE", "unknown"), (["SECRET_PHASE"], "unknown")):
            with tempfile.TemporaryDirectory() as folder:
                def scanner(*args, **kwargs):
                    error = TimeoutError("SECRET packet text and credentials")
                    error.collector_read_phase = supplied
                    raise error
                logs = []
                result = run_collection(folder, [server], {server["key"]}, ("SECRET_USER", "SECRET_PASSWORD"), {},
                                        scanner=scanner, emit=lambda **item: logs.append(item))
                self.assertEqual(result, 1)
                finished = next(item for item in logs if item["phase"] == "finished")
                self.assertEqual(finished["readPhase"], expected)
                self.assertEqual(finished["errorCode"], "reply_timeout")
                manifest = json.loads((Path(folder) / "index.json").read_text(encoding="utf-8"))
                self.assertNotIn("readPhase", manifest["servers"][0])
                self.assertNotIn("SECRET", repr(logs) + repr(manifest))

    def test_failure_logs_keep_attempt_counts_separate_from_retained_rows_and_filter_unsafe_fields(self):
        server = load_servers()[0]
        supplied = {"transportFailure": "socket_reset", "transportOperation": "receive",
                    "attemptRowsFetched": 400, "attemptRowsCompleted": 200,
                    "heartbeatsReceived": 3, "heartbeatAcksSent": 2,
                    "sessionElapsedSeconds": 642.0, "lastReceiveAgeSeconds": 1.5,
                    "lastSuccessfulSendAgeSeconds": None,
                    "lastHeartbeatReceiveAgeSeconds": float("nan"),
                    "lastHeartbeatAckAgeSeconds": "SECRET raw packet",
                    "unreviewed": "SECRET account or packet"}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / (server["key"] + ".rankings.json")
            atomic_json(path, snapshot(server))
            original = path.read_bytes()
            def scanner(*args, **kwargs):
                error = ConnectionResetError(errno.ECONNRESET, "SECRET arbitrary exception")
                error.collector_read_phase = "public_vehicles"
                error.collector_diagnostics = supplied
                raise error
            logs = []
            self.assertEqual(run_collection(folder, [server], {server["key"]}, ("SECRET", "SECRET"), {},
                scanner=scanner, emit=lambda **item: logs.append(item)), 1)
            finished = next(item for item in logs if item["phase"] == "finished")
            self.assertEqual(finished["rows"], 2)
            self.assertEqual(finished["attemptRowsFetched"], 400)
            self.assertEqual(finished["attemptRowsCompleted"], 200)
            self.assertEqual(finished["transportFailure"], "socket_reset")
            self.assertEqual(finished["errorCode"], "connection_failed")
            self.assertEqual(finished["heartbeatAcksSent"], 2)
            self.assertNotIn("lastHeartbeatReceiveAgeSeconds", finished)
            self.assertNotIn("lastHeartbeatAckAgeSeconds", finished)
            manifest = json.loads((Path(folder) / "index.json").read_text(encoding="utf-8"))
            self.assertTrue(set(supplied).isdisjoint(manifest["servers"][0]))
            self.assertEqual(path.read_bytes(), original)
            self.assertNotIn("SECRET", repr(logs) + repr(manifest))

    def test_log_boundary_rejects_unsafe_diagnostic_categories_and_wrong_scalar_types(self):
        from collector.collect import failure_diagnostics
        error = OSError("SECRET")
        error.collector_diagnostics = {"transportFailure": ["SECRET"], "transportOperation": "SECRET",
                                       "attemptRowsFetched": "SECRET", "attemptRowsCompleted": True,
                                       "heartbeatsReceived": -1, "heartbeatAcksSent": [],
                                       "sessionElapsedSeconds": float("inf"), "lastReceiveAgeSeconds": -1}
        self.assertEqual(failure_diagnostics(error), {})


if __name__ == "__main__":
    unittest.main()
