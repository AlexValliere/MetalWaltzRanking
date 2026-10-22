"""Whole-run budget tests use invented peers and clocks; no game requests."""

from contextlib import redirect_stderr
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from collector.__main__ import main
from collector.collect import error_code, open_socket, run_collection
from collector.configuration import load_servers
from collector.snapshots import SNAPSHOT_SCHEMA, SCOPE, atomic_json
from collector.timing import CollectionDeadline, DEFAULT_MAX_DURATION_SECONDS, positive_duration
from test_connection import ScriptedConnection, primary_failure, fallback_name


def snapshot(server, name="Invented old"):
    return {"schema": SNAPSHOT_SCHEMA, "serverKey": server["key"], "aliases": server["aliases"],
            "serverNames": server["serverNames"], "fund": 5,
            "startedAtUtc": "2026-10-22T10:00:00Z", "completedAtUtc": "2026-10-22T10:01:00Z",
            "rankingEnumerationComplete": True, "namesComplete": True, "scope": SCOPE,
            "rows": [{"rank": 1, "name": name, "grandeur": 1000, "nameStatus": "resolved"}]}


class DeadlineTests(unittest.TestCase):
    def test_duration_is_positive_finite_with_150_minute_default(self):
        self.assertEqual(DEFAULT_MAX_DURATION_SECONDS, 9000)
        self.assertEqual(positive_duration("1.5"), 1.5)
        for value in ("nan", "inf", "-inf", "0", "-1"):
            with self.assertRaises(ValueError):
                positive_duration(value)

    def test_cli_rejects_invalid_budget_before_collection(self):
        for value in ("nan", "inf", "0", "-1"):
            with patch("collector.__main__.run_collection") as runner, redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    main(["collect", "--max-duration-seconds", value])
            self.assertEqual(caught.exception.code, 2)
            runner.assert_not_called()

    def test_cli_passes_custom_whole_run_budget(self):
        with patch("collector.__main__.run_collection", return_value=0) as runner:
            self.assertEqual(main(["collect", "--max-duration-seconds", "12.5"]), 0)
        self.assertEqual(runner.call_args.kwargs["max_duration_seconds"], 12.5)

    def test_expired_budget_never_connects(self):
        with patch("collector.collect.socket.create_connection") as connector:
            with self.assertRaises(CollectionDeadline):
                open_socket(load_servers()[0], deadline=1, clock=lambda: 1)
        connector.assert_not_called()

    def test_connect_timeout_is_clamped_and_alternate_not_started_after_expiry(self):
        current = [1.0]
        def connect(address, timeout):
            self.assertEqual(timeout, 0.25)
            current[0] += timeout
            raise OSError("Invented connect timeout")
        with patch("collector.collect.socket.create_connection", side_effect=connect) as connector:
            with self.assertRaises(CollectionDeadline):
                open_socket(load_servers()[0], deadline=1.25, clock=lambda: current[0])
        self.assertEqual(connector.call_count, 1)

    def test_connection_completed_after_deadline_is_closed_without_login(self):
        current, sock = [1.0], Mock()
        def connect(address, timeout):
            current[0] = 3.0
            return sock
        with patch("collector.collect.socket.create_connection", side_effect=connect):
            with self.assertRaises(CollectionDeadline):
                open_socket(load_servers()[0], deadline=2, clock=lambda: current[0])
        sock.close.assert_called_once()
        sock.sendall.assert_not_called()

    def test_send_and_cached_receive_stop_at_whole_run_deadline(self):
        connection = ScriptedConnection()
        connection.collection_deadline = connection.fake_clock.value
        connection.base_notification = b"cached"
        with self.assertRaises(CollectionDeadline):
            connection.send(324, connection.expected_login)
        with self.assertRaises(CollectionDeadline):
            connection.receive(362)
        self.assertEqual(connection.socket.writes, [])

    def test_throttle_cannot_send_after_remaining_budget(self):
        import struct
        connection = ScriptedConnection()
        connection.last_request_at = connection.fake_clock.value
        connection.collection_deadline = connection.fake_clock.value + 0.05
        with self.assertRaises(CollectionDeadline):
            connection.send(137, struct.pack("<HIB", 5, 0, 200))
        self.assertAlmostEqual(connection.fake_clock.value, connection.collection_deadline)
        self.assertEqual(connection.socket.writes, [])

    def test_primary_name_wait_does_not_recover_after_whole_run_deadline(self):
        connection = ScriptedConnection()
        connection.collection_deadline = connection.fake_clock.value + 1
        with self.assertRaises(CollectionDeadline):
            connection.fetch_names([1, 2])
        self.assertEqual(connection.sent, {15: 1})
        self.assertEqual(connection.primary_name_timeouts, 0)

    def test_ordinary_primary_timeout_still_recovers_within_shared_budget(self):
        connection = ScriptedConnection(fallback=(fallback_name(1, "Invented A"),), fallback_count=1)
        connection.collection_deadline = connection.fake_clock.value + 100
        connection.fetch_names([1])
        self.assertEqual(connection.primary_name_timeouts, 1)
        self.assertEqual(connection.sent, {15: 1, 16: 1})
        self.assertTrue(connection.name_resolved(1))

    def test_whole_run_deadline_interrupts_incomplete_fallback(self):
        connection = ScriptedConnection(primary=(primary_failure(1, 2),),
                                        fallback=(fallback_name(1, "Invented A"),), fallback_count=2)
        connection.collection_deadline = connection.fake_clock.value + 1
        with self.assertRaises(CollectionDeadline):
            connection.fetch_names([1, 2])
        self.assertEqual(connection.sent, {15: 1, 16: 2})
        self.assertEqual(connection.received[17], 1)
        self.assertEqual(error_code(CollectionDeadline()), "collection_deadline")

    def test_completed_current_and_unvisited_snapshots_preserved_on_shared_deadline(self):
        servers = load_servers()[:3]
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            prior = {}
            for server in servers:
                path = directory / (server["key"] + ".rankings.json")
                atomic_json(path, snapshot(server))
                prior[server["key"]] = path.read_bytes()
            current, visited, deadlines = [1.0], [], []
            def scanner(server, credentials, masks, *, progress, deadline, clock):
                visited.append(server["key"])
                deadlines.append(deadline)
                if len(visited) == 1:
                    current[0] += 5
                    return snapshot(server, "Invented fresh")
                current[0] += 6
                raise CollectionDeadline()
            logs = []
            result = run_collection(directory, servers, {s["key"] for s in servers}, ("invented", "invented"), {},
                                    scanner=scanner, emit=lambda **item: logs.append(item),
                                    max_duration_seconds=10, clock=lambda: current[0])
            self.assertEqual(result, 1)
            self.assertEqual(visited, [s["key"] for s in servers[:2]])
            self.assertEqual(deadlines, [11.0, 11.0])
            index = json.loads((directory / "index.json").read_text(encoding="utf-8"))
            self.assertEqual([s["refreshStatus"] for s in index["servers"]], ["ok", "failed", "failed"])
            for server in servers[1:]:
                entry = next(s for s in index["servers"] if s["key"] == server["key"])
                self.assertEqual(entry["errorCode"], "collection_deadline")
                self.assertEqual((directory / entry["file"]).read_bytes(), prior[server["key"]])
            first = directory / index["servers"][0]["file"]
            self.assertNotEqual(first.read_bytes(), prior[servers[0]["key"]])
            self.assertEqual([s["serverKey"] for s in logs if s["phase"] == "collecting"], visited)
            self.assertTrue(all(s["rows"] == 1 and s["namesResolved"] == 1 for s in index["servers"]))

    def test_already_expired_collection_marks_all_requested_without_scanning(self):
        servers = load_servers()[:2]
        with tempfile.TemporaryDirectory() as folder:
            scanner = Mock()
            ticks = iter((1, 2, 3))
            result = run_collection(folder, servers, {s["key"] for s in servers}, ("invented", "invented"), {},
                                    scanner=scanner, emit=lambda **item: None,
                                    max_duration_seconds=1, clock=lambda: next(ticks))
            self.assertEqual(result, 1)
            scanner.assert_not_called()
            index = json.loads((Path(folder) / "index.json").read_text(encoding="utf-8"))
            self.assertEqual([s["errorCode"] for s in index["servers"]], ["collection_deadline"] * 2)


if __name__ == "__main__":
    unittest.main()
