from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collector.__main__ import main
from collector.collect import CollectionStopped, enumerate_rankings, error_code, run_collection
from collector.configuration import load_servers
from collector.records import ServerRejection
from collector.snapshots import (INDEX_SCHEMA, SNAPSHOT_SCHEMA, SCOPE, atomic_json, snapshot_entry,
                                 validate_snapshot)


def snapshot(server, names=("Invented A", None)):
    return {"schema": SNAPSHOT_SCHEMA, "serverKey": server["key"], "aliases": server["aliases"],
            "serverNames": server["serverNames"], "fund": 5,
            "startedAtUtc": "2026-10-22T10:00:00Z", "completedAtUtc": "2026-10-22T10:01:00Z",
            "rankingEnumerationComplete": True, "namesComplete": all(names), "scope": SCOPE,
            "rows": [{"rank": i + 1, "name": name, "grandeur": 1000 - i,
                      "nameStatus": "resolved" if name else "unavailable"} for i, name in enumerate(names)]}


class SyntheticRanking:
    def __init__(self, count, clamp=200):
        self.rows = [(index + 1, count - index) for index in range(count)]
        self.offsets, self.named, self.clamp = [], [], clamp

    def fetch_rank_page(self, offset, count=200):
        self.offsets.append((offset, count))
        return self.rows[offset:offset + min(count, self.clamp)]

    def fetch_names(self, players):
        self.named.extend(players)


class EnumerationTests(unittest.TestCase):
    def test_clamped_pages_advance_actual_count_and_require_empty(self):
        connection = SyntheticRanking(413, clamp=100)
        self.assertEqual(enumerate_rankings(connection), connection.rows)
        self.assertEqual(connection.offsets, [(0, 200), (100, 200), (200, 200), (199, 2),
                                              (300, 200), (400, 200), (413, 200)])
        self.assertEqual(connection.named, list(range(1, 414)))

    def test_short_and_zero_and_exact_pages_require_empty(self):
        for count, expected in ((0, [(0, 200)]), (7, [(0, 200), (7, 200)]),
                                (200, [(0, 200), (200, 200)])):
            connection = SyntheticRanking(count)
            self.assertEqual(len(enumerate_rankings(connection)), count)
            self.assertEqual(connection.offsets, expected)

    def test_ignored_offset_and_duplicate_stop(self):
        connection = SyntheticRanking(400)
        connection.fetch_rank_page = lambda offset, count=200: connection.rows[:count]
        with self.assertRaises(CollectionStopped) as caught:
            enumerate_rankings(connection)
        self.assertEqual(caught.exception.code, "ranking_changed_or_offset_ignored")

    def test_overlap_change_stops(self):
        connection = SyntheticRanking(400)
        original = connection.fetch_rank_page
        connection.fetch_rank_page = lambda offset, count=200: [(900, 900)] if count == 2 else original(offset, count)
        with self.assertRaises(CollectionStopped) as caught:
            enumerate_rankings(connection)
        self.assertEqual(caught.exception.code, "ranking_overlap_changed")

    def test_score_increase_stops_without_sorting(self):
        connection = SyntheticRanking(7)
        connection.rows[4] = (5, 999)
        with self.assertRaises(CollectionStopped) as caught:
            enumerate_rankings(connection)
        self.assertEqual(caught.exception.code, "ranking_score_order_changed")

    def test_row_limit_stops(self):
        connection = SyntheticRanking(7)
        with patch("collector.collect.MAX_ROWS", 6):
            with self.assertRaises(CollectionStopped) as caught:
                enumerate_rankings(connection)
        self.assertEqual(caught.exception.code, "row_limit")


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.servers = load_servers()
        self.server = self.servers[0]
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def seed(self, server=None):
        server = server or self.server
        value = snapshot(server)
        path = self.directory / (server["key"] + ".rankings.json")
        atomic_json(path, value)
        return path

    def test_partial_names_are_explicit_and_ids_rejected(self):
        value = snapshot(self.server)
        self.assertEqual(validate_snapshot(value, self.server), 1)
        value["rows"][0]["playerId"] = 1234
        with self.assertRaises(ValueError):
            validate_snapshot(value, self.server)

    def test_incomplete_enumeration_and_incorrect_name_counts_rejected(self):
        for mutate in (lambda value: value.update(rankingEnumerationComplete=False),
                       lambda value: value.update(namesComplete=True),
                       lambda value: value["rows"][1].update(nameStatus="missing"),
                       lambda value: value["rows"][1].update(name=""),
                       lambda value: value["rows"][0].update(rank=2),
                       lambda value: value["rows"][0].update(grandeur=1)):
            value = snapshot(self.server)
            mutate(value)
            with self.assertRaises(ValueError):
                validate_snapshot(value, self.server)

    def test_manifest_counts_hash_derive_from_file(self):
        path = self.seed()
        entry = snapshot_entry(self.directory, self.server)
        self.assertEqual((entry["rows"], entry["namesResolved"], entry["namesMissing"]), (2, 1, 1))
        self.assertEqual(entry["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(entry["refreshStatus"], "seed")

    def test_failed_refresh_preserves_last_good_bytes(self):
        path = self.seed()
        before = path.read_bytes()
        def failed(*args, **kwargs):
            raise TimeoutError("This unsafe exception could contain SECRET or packet bytes")
        logs = []
        result = run_collection(self.directory, self.servers, {self.server["key"]}, ("SECRET", "PASSWORD"), {},
                                scanner=failed, emit=lambda **fields: logs.append(fields))
        self.assertEqual(result, 1)
        self.assertEqual(path.read_bytes(), before)
        index_text = (self.directory / "index.json").read_text(encoding="utf-8")
        self.assertNotIn("SECRET", index_text + repr(logs))
        index = json.loads(index_text)
        entry = index["servers"][0]
        self.assertEqual(entry["refreshStatus"], "failed")
        self.assertEqual(entry["errorCode"], "reply_timeout")
        self.assertEqual(entry["sha256"], hashlib.sha256(before).hexdigest())
        self.assertIn("lastAttemptAtUtc", entry)

    def test_incomplete_scan_never_overwrites_good_file(self):
        path = self.seed()
        before = path.read_bytes()
        bad = snapshot(self.server)
        bad["rankingEnumerationComplete"] = False
        result = run_collection(self.directory, [self.server], {self.server["key"]}, ("invented", "invented"), {},
                                scanner=lambda *args, **kwargs: bad, emit=lambda **fields: None)
        self.assertEqual(result, 1)
        self.assertEqual(path.read_bytes(), before)

    def test_success_refresh_and_partial_names(self):
        self.seed()
        value = snapshot(self.server, ("Invented 名", None, "Invented C"))
        result = run_collection(self.directory, [self.server], {self.server["key"]}, ("invented", "invented"), {},
                                scanner=lambda *args, **kwargs: value, emit=lambda **fields: None)
        self.assertEqual(result, 0)
        entry = json.loads((self.directory / "index.json").read_text(encoding="utf-8"))["servers"][0]
        self.assertEqual((entry["refreshStatus"], entry["rows"], entry["namesMissing"]), ("ok", 3, 1))
        self.assertNotIn("errorCode", entry)

    def test_one_failure_does_not_prevent_other_backend_success(self):
        first, second = self.servers[:2]
        first_path = self.seed(first)
        first_bytes = first_path.read_bytes()
        def scanner(server, *args, **kwargs):
            if server["key"] == first["key"]:
                raise ServerRejection("login", 12)
            return snapshot(server, ("Invented B",))
        result = run_collection(self.directory, [first, second], {first["key"], second["key"]},
                                ("invented", "invented"), {}, scanner=scanner, emit=lambda **fields: None)
        self.assertEqual(result, 1)
        index = json.loads((self.directory / "index.json").read_text(encoding="utf-8"))
        self.assertEqual([item["refreshStatus"] for item in index["servers"]], ["failed", "ok"])
        self.assertEqual(first_path.read_bytes(), first_bytes)

    def test_unselected_attempt_metadata_preserved_counts_recomputed(self):
        first, second = self.servers[:2]
        self.seed(first)
        self.seed(second)
        old = {"schema": INDEX_SCHEMA, "generatedAtUtc": "2026-10-22T10:02:00Z", "servers": [
            {"key": second["key"], "rows": 999999, "refreshStatus": "failed", "errorCode": "reply_timeout",
             "lastAttemptAtUtc": "2026-10-22T10:02:00Z"}]}
        atomic_json(self.directory / "index.json", old)
        run_collection(self.directory, [first, second], {first["key"]}, ("invented", "invented"), {},
                       scanner=lambda server, *args, **kwargs: snapshot(server), emit=lambda **fields: None)
        entry = json.loads((self.directory / "index.json").read_text(encoding="utf-8"))["servers"][1]
        self.assertEqual(entry["rows"], 2)
        self.assertEqual(entry["refreshStatus"], "failed")
        self.assertEqual(entry["lastAttemptAtUtc"], "2026-10-22T10:02:00Z")

    def test_missing_credentials_updates_status_without_network(self):
        path = self.seed()
        before = path.read_bytes()
        with patch.dict("os.environ", {}, clear=True), patch("collector.collect.open_socket") as opener:
            with redirect_stdout(StringIO()):
                code = main(["collect", "--output", str(self.directory), "--server", "Bonn"])
        self.assertEqual(code, 1)
        opener.assert_not_called()
        self.assertEqual(path.read_bytes(), before)
        entry = json.loads((self.directory / "index.json").read_text(encoding="utf-8"))["servers"][0]
        self.assertEqual(entry["errorCode"], "credentials_missing")

    def test_atomic_write_leaves_no_temporary_files(self):
        self.seed()
        self.assertEqual([p.name for p in self.directory.iterdir()], [self.server["key"] + ".rankings.json"])

    def test_exception_details_are_never_public(self):
        self.assertEqual(error_code(RuntimeError("USERNAME PASSWORD raw data")), "collector_failed")
        self.assertEqual(error_code(ServerRejection("login", 12)), "server_login_12")
        self.assertEqual(error_code(ValueError("packet bytes")), "protocol_or_snapshot_invalid")


if __name__ == "__main__":
    unittest.main()
