"""Pre-login failures against invented sockets; no game/network requests."""

import errno
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

from collector.collect import error_code, failure_diagnostics, open_socket, run_collection, scan_server
from collector.configuration import load_masks, load_servers
from collector.connection_setup import (CONNECT_FAILURE_FIELDS, ConnectionSetupDiagnostics,
                                         connect_failure_reason)
from collector.snapshots import atomic_json
from collector.timing import CollectionDeadline
from test_collection import snapshot


SERVER = {"address": "SECRET primary address", "ports": [1111, 2222],
          "alternateRoutes": [{"address": "SECRET alternate address", "ports": [3333, 4444]}]}


class SetupSocket:
    def __init__(self, setup_error=None):
        self.setup_error, self.closed, self.writes = setup_error, False, []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.closed = True

    def settimeout(self, value):
        if self.setup_error is not None:
            raise self.setup_error

    def sendall(self, data):
        self.writes.append(data)


class ConnectionSetupTests(unittest.TestCase):
    def test_errno_types_and_windows_codes_use_fixed_categories_not_messages(self):
        cases = [(ConnectionRefusedError("SECRET"), "refused"),
                 (OSError(errno.ECONNREFUSED, "SECRET"), "refused"),
                 (OSError(errno.ENETUNREACH, "SECRET"), "unreachable"),
                 (OSError(errno.EHOSTUNREACH, "SECRET"), "unreachable"),
                 (OSError(errno.ENETDOWN, "SECRET"), "unreachable"),
                 (TimeoutError("SECRET"), "timeout"),
                 (OSError(errno.ETIMEDOUT, "SECRET"), "timeout"),
                 (socket.gaierror(-2, "SECRET"), "name_resolution"),
                 (ConnectionResetError("SECRET"), "reset"),
                 (PermissionError(errno.EACCES, "SECRET"), "other"),
                 (OSError("ECONNREFUSED SECRET text alone is not proof"), "other")]
        for number, reason in ((10061, "refused"), (10060, "timeout"), (10050, "unreachable"),
                               (10051, "unreachable"), (10065, "unreachable"), (10054, "reset")):
            error = OSError("SECRET")
            error.winerror = number
            cases.append((error, reason))
        for error, reason in cases:
            with self.subTest(reason=reason, kind=type(error).__name__):
                self.assertEqual(connect_failure_reason(error), reason)

    def test_all_reviewed_endpoints_refused_once_in_order_without_payloads_or_retries(self):
        error = ConnectionRefusedError(errno.ECONNREFUSED, "SECRET endpoint, packet and credentials")
        with patch("collector.collect.socket.create_connection", side_effect=error) as connector:
            with self.assertRaises(ConnectionError) as caught:
                open_socket(SERVER)
        self.assertEqual([call.args[0] for call in connector.call_args_list],
                         [("SECRET primary address", 1111), ("SECRET primary address", 2222),
                          ("SECRET alternate address", 3333), ("SECRET alternate address", 4444)])
        self.assertTrue(all(call.kwargs == {"timeout": 5} for call in connector.call_args_list))
        self.assertEqual(error_code(caught.exception), "connection_failed")
        values = failure_diagnostics(caught.exception)
        self.assertEqual(values["connectAttempts"], 4)
        self.assertEqual(values["connectRefused"], 4)
        self.assertEqual(values["connectLastFailureEndpointOrdinal"], 4)
        self.assertEqual(values["connectFailureReason"], "refused")
        self.assertEqual(values["connectFailureOperation"], "connect")
        self.assertNotIn("SECRET", json.dumps(values))
        self.assertNotIn("1111", json.dumps(values))

    def test_unreachable_timeout_name_resolution_reset_and_other_are_counted(self):
        cases = [(OSError(errno.EHOSTUNREACH, "SECRET"), "unreachable"),
                 (TimeoutError("SECRET"), "timeout"),
                 (socket.gaierror(-2, "SECRET"), "name_resolution"),
                 (ConnectionResetError(errno.ECONNRESET, "SECRET"), "reset"),
                 (OSError(errno.EIO, "SECRET"), "other")]
        for error, reason in cases:
            with self.subTest(reason=reason), \
                    patch("collector.collect.socket.create_connection", side_effect=error) as connector, \
                    self.assertRaises(ConnectionError) as caught:
                open_socket({"address": "SECRET", "ports": [1111, 2222]})
            values = failure_diagnostics(caught.exception)
            self.assertEqual(connector.call_count, 2)
            self.assertEqual(values["connectAttempts"], 2)
            self.assertEqual(values[CONNECT_FAILURE_FIELDS[reason]], 2)
            self.assertEqual(sum(values[key] for key in CONNECT_FAILURE_FIELDS.values()), 2)
            self.assertEqual(values["connectFailureReason"], reason)
            self.assertNotIn("SECRET", repr(values))

    def test_mixed_failures_preserve_counts_and_last_failure_ordinal(self):
        errors = [ConnectionRefusedError(errno.ECONNREFUSED, "SECRET"),
                  OSError(errno.ENETUNREACH, "SECRET"), TimeoutError("SECRET"),
                  socket.gaierror(-2, "SECRET")]
        with patch("collector.collect.socket.create_connection", side_effect=errors), \
                self.assertRaises(ConnectionError) as caught:
            open_socket(SERVER)
        self.assertEqual(failure_diagnostics(caught.exception), {
            "connectAttempts": 4, "connectRefused": 1, "connectUnreachable": 1, "connectTimeouts": 1,
            "connectNameResolutionFailures": 1, "connectResets": 0, "connectOtherFailures": 0,
            "connectLastFailureEndpointOrdinal": 4, "connectFailureReason": "name_resolution",
            "connectFailureOperation": "connect"})

    def test_first_tcp_success_stops_all_route_attempts(self):
        peer = SetupSocket()
        state = ConnectionSetupDiagnostics()
        with patch("collector.collect.socket.create_connection", return_value=peer) as connector:
            self.assertIs(open_socket(SERVER, diagnostics=state), peer)
        self.assertEqual(connector.call_count, 1)
        self.assertEqual(state.attempts, 1)
        self.assertFalse(state.failures)
        self.assertFalse(peer.closed)
        self.assertFalse(peer.writes)

    def test_expired_budget_does_not_attempt_any_endpoint(self):
        with patch("collector.collect.socket.create_connection") as connector, \
                self.assertRaises(CollectionDeadline) as caught:
            open_socket(SERVER, deadline=1, clock=lambda: 1)
        connector.assert_not_called()
        self.assertEqual(failure_diagnostics(caught.exception), {})

    def test_shared_deadline_clamps_attempt_and_prevents_next_endpoint(self):
        current = [1.0]
        def connect(address, timeout):
            self.assertEqual(timeout, 0.25)
            current[0] += timeout
            raise TimeoutError("SECRET timeout, address and credentials")
        with patch("collector.collect.socket.create_connection", side_effect=connect) as connector, \
                self.assertRaises(CollectionDeadline) as caught:
            open_socket(SERVER, deadline=1.25, clock=lambda: current[0])
        self.assertEqual(connector.call_count, 1)
        self.assertEqual(error_code(caught.exception), "collection_deadline")
        self.assertEqual(failure_diagnostics(caught.exception)["connectTimeouts"], 1)

    def test_late_success_closes_socket_and_never_starts_another_attempt(self):
        current, peer = [1.0], SetupSocket()
        def connect(address, timeout):
            current[0] = 3
            return peer
        with patch("collector.collect.socket.create_connection", side_effect=connect) as connector, \
                self.assertRaises(CollectionDeadline) as caught:
            open_socket(SERVER, deadline=2, clock=lambda: current[0])
        self.assertEqual(connector.call_count, 1)
        self.assertTrue(peer.closed)
        self.assertFalse(peer.writes)
        values = failure_diagnostics(caught.exception)
        self.assertEqual(values["connectAttempts"], 1)
        self.assertEqual(sum(values[key] for key in CONNECT_FAILURE_FIELDS.values()), 0)
        self.assertNotIn("connectFailureReason", values)

    def test_close_error_cannot_replace_the_shared_deadline(self):
        current, peer = [1.0], Mock()
        peer.close.side_effect = OSError("SECRET close error")
        def connect(address, timeout):
            current[0] = 3
            return peer
        with patch("collector.collect.socket.create_connection", side_effect=connect), \
                self.assertRaises(CollectionDeadline):
            open_socket(SERVER, deadline=2, clock=lambda: current[0])
        peer.close.assert_called_once()
        peer.sendall.assert_not_called()

    def test_socket_setup_failure_closes_connected_socket_without_another_route_or_login(self):
        peer = SetupSocket(OSError(errno.EIO, "SECRET setup and endpoint"))
        with patch("collector.collect.socket.create_connection", return_value=peer) as connector, \
                self.assertRaises(OSError) as caught:
            scan_server(load_servers()[0], ("SECRET", "SECRET"), load_masks())
        self.assertTrue(peer.closed)
        self.assertFalse(peer.writes)
        self.assertEqual(connector.call_count, 1)
        self.assertEqual(caught.exception.collector_read_phase, "connect")
        values = failure_diagnostics(caught.exception)
        self.assertEqual(values["connectAttempts"], 1)
        self.assertEqual(values["connectOtherFailures"], 1)
        self.assertEqual(values["connectFailureOperation"], "socket_setup")
        self.assertNotIn("SECRET", repr(values))

    def test_session_constructor_failure_closes_socket_and_does_not_reconnect(self):
        peer = SetupSocket()
        with patch("collector.collect.socket.create_connection", return_value=peer) as connector, \
                patch("collector.collect.CollectorConnection", side_effect=ConnectionResetError(errno.ECONNRESET, "SECRET")), \
                self.assertRaises(ConnectionResetError) as caught:
            scan_server(load_servers()[0], ("SECRET", "SECRET"), load_masks())
        self.assertTrue(peer.closed)
        self.assertFalse(peer.writes)
        self.assertEqual(connector.call_count, 1)
        values = failure_diagnostics(caught.exception)
        self.assertEqual(values["connectResets"], 1)
        self.assertEqual(values["connectFailureOperation"], "session_initialization")

    def test_deadline_from_connector_is_not_counted_as_a_socket_timeout(self):
        with patch("collector.collect.socket.create_connection", side_effect=CollectionDeadline()) as connector, \
                self.assertRaises(CollectionDeadline) as caught:
            open_socket(SERVER)
        self.assertEqual(connector.call_count, 1)
        self.assertEqual(failure_diagnostics(caught.exception)["connectTimeouts"], 0)
        self.assertNotIn("connectFailureReason", failure_diagnostics(caught.exception))

    def test_log_whitelist_merges_safe_session_fields_and_rejects_unsafe_setup_values(self):
        error = OSError("SECRET")
        error.collector_diagnostics = {
            "connectAttempts": 2, "connectRefused": 1, "connectUnreachable": True,
            "connectTimeouts": -1, "connectOtherFailures": "SECRET", "connectNameResolutionFailures": [],
            "connectLastFailureEndpointOrdinal": 2, "connectFailureReason": ["SECRET"],
            "connectFailureOperation": "SECRET endpoint", "heartbeatsReceived": 3,
            "sessionElapsedSeconds": 4.5, "address": "SECRET", "errno": 123456789}
        self.assertEqual(failure_diagnostics(error), {"connectAttempts": 2, "connectRefused": 1,
            "connectLastFailureEndpointOrdinal": 2, "heartbeatsReceived": 3, "sessionElapsedSeconds": 4.5})

    def test_failed_prelogin_attempt_logs_safe_counts_and_retains_last_good_snapshot(self):
        server, logs = load_servers()[0], []
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / (server["key"] + ".rankings.json")
            atomic_json(path, snapshot(server))
            original = path.read_bytes()
            with patch("collector.collect.socket.create_connection", side_effect=ConnectionRefusedError(
                    errno.ECONNREFUSED, "SECRET endpoint, account, packet and credentials")) as connector:
                code = run_collection(folder, [server], {server["key"]}, ("SECRET", "SECRET"), load_masks(),
                                      emit=lambda **values: logs.append(values))
            self.assertEqual(code, 1)
            self.assertEqual(connector.call_count, len(server["ports"]))
            finished = logs[-1]
            self.assertEqual(finished["errorCode"], "connection_failed")
            self.assertEqual(finished["readPhase"], "connect")
            self.assertEqual(finished["connectAttempts"], len(server["ports"]))
            self.assertEqual(finished["connectRefused"], len(server["ports"]))
            self.assertEqual(finished["rows"], 2)
            self.assertEqual(path.read_bytes(), original)
            manifest = json.loads((Path(folder) / "index.json").read_text(encoding="utf-8"))
            self.assertFalse(any(key.startswith("connect") for key in manifest["servers"][0]))
            encoded = json.dumps(logs) + json.dumps(manifest)
            self.assertNotIn("SECRET", encoded)
            self.assertNotIn(server["address"], encoded)


if __name__ == "__main__":
    unittest.main()
