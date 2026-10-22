"""Failure phases are fixed strings in finished logs, never public metadata."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collector.collect import run_collection, scan_server
from collector.configuration import load_masks, load_servers
from collector.connection import CollectorConnection
from test_connection import ScriptedConnection
from test_session import InventedGameSocket


class DiagnosticTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
