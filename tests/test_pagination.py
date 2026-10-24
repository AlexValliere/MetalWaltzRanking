"""Fast ranking enumeration and later bounded enrichment against invented peers."""

import struct
import unittest
from unittest.mock import patch

from collector.collect import CollectionStopped, enumerate_rankings, scan_server
from collector.configuration import load_masks, load_servers
from collector.connection import CollectorConnection
from collector.timing import CollectionDeadline
from test_batch_pacing import Clock
from test_collection import SyntheticRanking
from test_session import InventedGameSocket


class RecordingRanking(SyntheticRanking):
    def __init__(self, count, clamp=200):
        super().__init__(count, clamp)
        self.events = []

    def fetch_rank_page(self, offset, count=200):
        self.events.append(("rank", offset, count))
        return super().fetch_rank_page(offset, count)

    def fetch_names(self, players):
        self.events.append(("names", tuple(players)))
        super().fetch_names(players)

    def fetch_vehicles(self, players):
        self.events.append(("vehicles", tuple(players)))


class DeadlineRanking(RecordingRanking):
    """Use the production deadline check with a monotonic invented clock."""
    check_deadlines = CollectorConnection.check_deadlines

    def __init__(self, count):
        super().__init__(count)
        self.fake_clock = Clock()
        self.clock = self.fake_clock.monotonic
        self.started = self.clock()
        self.collection_deadline, self.session_seconds = self.started + 10, 3600


class PaginationTests(unittest.TestCase):
    def assert_no_profiles(self, connection):
        self.assertTrue(all(event[0] == "rank" for event in connection.events))
        self.assertEqual(connection.named, [])
        self.assertEqual(connection.ranking_rows_completed, 0)
        self.assertEqual(connection.read_phase, "rankings")

    def test_rank_changes_during_slow_enrichment_cannot_change_frozen_page_offsets(self):
        class MovingRanking(RecordingRanking):
            def fetch_names(self, players):
                super().fetch_names(players)
                # A newly promoted player would shift the next live offset and
                # duplicate an earlier identity in the old interleaved design.
                self.rows.insert(0, self.rows.pop())

        connection = MovingRanking(450)
        original = list(connection.rows)
        result = enumerate_rankings(connection)
        self.assertEqual(result, original)
        self.assertNotEqual(connection.rows, original)
        self.assertEqual(connection.offsets, [(0, 200), (200, 200), (199, 2), (400, 200), (450, 200)])
        first_profile = next(i for i, event in enumerate(connection.events) if event[0] != "rank")
        self.assertTrue(all(event[0] == "rank" for event in connection.events[:first_profile]))
        self.assertFalse(any(event[0] == "rank" for event in connection.events[first_profile:]))
        self.assertEqual([len(event[1]) for event in connection.events if event[0] == "names"], [200, 200, 50])
        self.assertEqual(connection.named, list(range(1, 451)))
        self.assertEqual((connection.ranking_rows_fetched, connection.ranking_rows_completed), (450, 450))

    def test_clamped_ranking_pages_still_enrich_in_at_most_200_row_cohorts(self):
        connection = RecordingRanking(413, clamp=100)
        self.assertEqual(enumerate_rankings(connection), connection.rows)
        self.assertEqual(connection.offsets, [(0, 200), (100, 200), (200, 200), (199, 2),
                                              (300, 200), (400, 200), (413, 200)])
        for kind in ("names", "vehicles"):
            self.assertEqual([len(event[1]) for event in connection.events if event[0] == kind], [200, 200, 13])
        self.assertEqual(connection.named, list(range(1, 414)))

    def test_duplicate_overlap_score_and_row_limit_fail_before_any_profile_query(self):
        for mode, expected in (("duplicate", "ranking_changed_or_offset_ignored"),
                               ("overlap", "ranking_overlap_changed"),
                               ("score", "ranking_score_order_changed"), ("limit", "row_limit")):
            connection = RecordingRanking(400)
            original = connection.fetch_rank_page
            if mode == "duplicate":
                connection.fetch_rank_page = lambda offset, count=200: original(0, count)
            elif mode == "overlap":
                connection.fetch_rank_page = lambda offset, count=200: ([(9999, 999)] if count == 2
                                                                        else original(offset, count))
            elif mode == "score":
                connection.rows[-1] = (400, 10000)
            with self.subTest(mode=mode), patch("collector.collect.MAX_ROWS", 399 if mode == "limit" else 1_000_000):
                with self.assertRaises(CollectionStopped) as caught:
                    enumerate_rankings(connection)
                self.assertEqual(caught.exception.code, expected)
                self.assert_no_profiles(connection)

    def test_malformed_later_ranking_page_fails_before_profile_queries(self):
        class MalformedRanking(RecordingRanking):
            def fetch_rank_page(self, offset, count=200):
                if offset:
                    raise ValueError("Invented malformed ranking")
                return super().fetch_rank_page(offset, count)
        connection = MalformedRanking(400)
        with self.assertRaises(ValueError):
            enumerate_rankings(connection)
        self.assert_no_profiles(connection)
        self.assertEqual(connection.ranking_rows_fetched, 200)

    def test_progress_describes_completed_enrichment_not_early_rank_reads(self):
        connection, progress = RecordingRanking(5201), []
        def completed(count):
            progress.append((count, connection.ranking_rows_fetched, connection.ranking_rows_completed,
                             connection.events[-1][0], connection.read_phase))
        enumerate_rankings(connection, progress=completed)
        self.assertEqual(progress, [(5000, 5201, 5000, "vehicles", "public_vehicles"),
                                    (5201, 5201, 5201, "vehicles", "public_vehicles")])

    def test_names_only_mode_keeps_canonical_order_and_never_queries_vehicles(self):
        connection = RecordingRanking(413)
        result = enumerate_rankings(connection, collect_vehicles=False)
        self.assertEqual(result, connection.rows)
        self.assertEqual(connection.named, list(range(1, 414)))
        self.assertFalse(any(event[0] == "vehicles" for event in connection.events))
        self.assertEqual(connection.read_phase, "primary_names")
        self.assertEqual(connection.ranking_rows_completed, 413)

    def test_empty_ranking_never_starts_profile_transactions_or_progress(self):
        connection, progress = RecordingRanking(0), []
        self.assertEqual(enumerate_rankings(connection, progress=progress.append), [])
        self.assert_no_profiles(connection)
        self.assertEqual(connection.offsets, [(0, 200)])
        self.assertEqual(progress, [])

    def test_expired_deadline_after_enumeration_prevents_every_profile_query(self):
        class ExpiringRanking(DeadlineRanking):
            def fetch_rank_page(self, offset, count=200):
                page = super().fetch_rank_page(offset, count)
                if not page:
                    self.fake_clock.value = self.collection_deadline
                return page
        connection = ExpiringRanking(450)
        with self.assertRaises(CollectionDeadline):
            enumerate_rankings(connection)
        self.assert_no_profiles(connection)
        self.assertEqual(connection.ranking_rows_fetched, 450)

    def test_deadline_between_profile_cohorts_stops_before_the_next_name_query(self):
        class ExpiringRanking(DeadlineRanking):
            def fetch_vehicles(self, players):
                super().fetch_vehicles(players)
                self.fake_clock.value = self.collection_deadline
        connection = ExpiringRanking(450)
        with self.assertRaises(CollectionDeadline):
            enumerate_rankings(connection)
        self.assertEqual(connection.ranking_rows_fetched, 450)
        self.assertEqual(connection.ranking_rows_completed, 200)
        self.assertEqual([event[0] for event in connection.events if event[0] != "rank"], ["names", "vehicles"])

    def test_profile_failure_keeps_full_rank_count_without_retry_or_later_profile_query(self):
        class FailedProfiles(RecordingRanking):
            def fetch_vehicles(self, players):
                super().fetch_vehicles(players)
                if players[0] == 201:
                    raise TimeoutError("Invented missing detail reply")
        connection = FailedProfiles(450)
        with self.assertRaises(TimeoutError):
            enumerate_rankings(connection)
        self.assertEqual((connection.ranking_rows_fetched, connection.ranking_rows_completed), (450, 200))
        self.assertEqual(connection.read_phase, "public_vehicles")
        self.assertEqual([event[0] for event in connection.events if event[0] != "rank"],
                         ["names", "vehicles", "names", "vehicles"])

    def test_final_names_only_cohort_still_checks_deadline_before_returning(self):
        class ExpiringNames(DeadlineRanking):
            def fetch_names(self, players):
                super().fetch_names(players)
                self.fake_clock.value = self.collection_deadline
        connection = ExpiringNames(3)
        with self.assertRaises(CollectionDeadline):
            enumerate_rankings(connection, collect_vehicles=False)
        self.assertEqual(connection.ranking_rows_fetched, 3)
        self.assertFalse(any(event[0] == "vehicles" for event in connection.events))

    def test_encrypted_invalid_rank_sequence_never_sends_name_or_detail_requests(self):
        class InvalidRankingSocket(InventedGameSocket):
            def __init__(self, masks, mode):
                super().__init__(masks)
                self.mode = mode
                if mode == "score":
                    self.ranks[1] = (102, 1000)
            def reply(self, opcode, payload=b""):
                if opcode == 131 and len(self.rank_requests) == 2:
                    if self.mode == "malformed":
                        payload = payload[:-1]
                    elif self.mode == "duplicate":
                        payload = struct.pack("<HBIiI", 0, 1, 101, 900, 0)
                super().reply(opcode, payload)
        masks = load_masks()
        for mode in ("malformed", "duplicate", "score"):
            peer = InvalidRankingSocket(masks, mode)
            with self.subTest(mode=mode), patch("collector.collect.open_socket", return_value=peer), \
                    self.assertRaises((ValueError, CollectionStopped)) as caught:
                scan_server(load_servers()[0], ("invented", "invented"), masks)
            self.assertEqual(caught.exception.collector_read_phase, "rankings")
            self.assertEqual(peer.sent[15], 0)
            self.assertEqual(peer.sent[16], 0)
            self.assertEqual(peer.sent[20], 0)
            self.assertTrue(peer.closed)


if __name__ == "__main__":
    unittest.main()
