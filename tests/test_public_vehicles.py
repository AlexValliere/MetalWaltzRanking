import socket
import struct
import unittest
from unittest.mock import patch

from collector.collect import scan_server
from collector.records import detail_records
from collector.snapshots import validate_snapshot
from test_collection import snapshot
from test_connection import Clock, SyntheticSocket
from collector.configuration import load_masks, load_servers
from collector.connection import CollectorConnection
from collector.cipher import GameDes
from collector.transport import TransportEncoder
from test_session import InventedGameSocket


def descriptor(player, mask=0x0c, vehicles=(), tactics=()):
    body = struct.pack("<IB", player, mask)
    if mask & 4:
        body += bytes([len(vehicles)])
        for capsule, phase, faculties in vehicles:
            body += struct.pack("<IBB", capsule, phase, len(faculties))
            body += b"".join(struct.pack("<BHi", *faculty) for faculty in faculties)
    if mask & 8:
        body += bytes([len(tactics)]) + b"".join(struct.pack("<II", *pair) for pair in tactics)
    return body


def reply(*descriptions):
    return 19, bytes([len(descriptions)]) + b"".join(descriptions)


class VehicleConnection(CollectorConnection):
    def __init__(self, batches=(), fallback_names=None, *, vehicle_fields=0x0c, vehicle_batch_size=50,
                 vehicle_zero_recovery=False):
        self.fake_clock = Clock()
        super().__init__(SyntheticSocket(self.fake_clock), load_masks(), GameDes(), b"invented-login",
                         clock=self.fake_clock.monotonic, sleeper=self.fake_clock.sleep,
                         vehicle_fields=vehicle_fields, vehicle_batch_size=vehicle_batch_size,
                         vehicle_zero_recovery=vehicle_zero_recovery)
        self.batches = iter(batches)
        self.vehicle_requests = []
        self.fallback_names = fallback_names or {}

    def send(self, opcode, payload=b""):
        super().send(opcode, payload)
        if opcode == 20:
            self.vehicle_requests.append(payload)
            self.messages.extend(next(self.batches, ()))
        elif opcode == 16:
            player, = struct.unpack("<I", payload)
            name = self.fallback_names.get(player)
            self.messages.append((17, struct.pack("<HI", 0, player) + name.encode() + b"\0\0"
                                  if name is not None else struct.pack("<H", 48)))


class PublicVehicleTests(unittest.TestCase):
    def test_zero_recovery_reuses_actual_successful_nickname_without_another16(self):
        for fields in (4, 12):
            connection = VehicleConnection([
                (reply(descriptor(1, 0)),),
                (reply(descriptor(1, fields, vehicles=[(8311, 3, [])])),),
            ], fallback_names={1: "Invented"}, vehicle_fields=fields, vehicle_zero_recovery=True)
            connection.name_masks[1] = 0
            connection.fetch_nickname_fallback([1])
            with self.subTest(fields=fields):
                self.assertEqual(connection.fallback_successes, {1})
                connection.fetch_vehicles([1])
                self.assertEqual(connection.sent, {16: 1, 20: 2})
                self.assertEqual(connection.vehicle_requests, [struct.pack("<BIB", 1, 1, fields)] * 2)
                self.assertEqual(connection.vehicle_masks, {1: fields})
                self.assertEqual(connection.vehicles[1][0]["capsuleTypeId"], 8311)
                self.assertFalse(connection.vehicle_failures)

    def test_already_queued_real_fields_and_heartbeat_prevent_an_unnecessary_requery(self):
        connection = VehicleConnection([
            (reply(descriptor(1, 0)), (65530, b""), reply(descriptor(1, 12))),
        ], fallback_names={1: "Invented"}, vehicle_zero_recovery=True)
        connection.name_masks[1] = 0
        connection.fetch_nickname_fallback([1])
        connection.fetch_vehicles([1])
        self.assertEqual(connection.sent, {16: 1, 20: 1, 65531: 1})
        self.assertEqual(connection.vehicle_masks, {1: 12})
        self.assertFalse(connection.messages)

    def test_real_fields_queued_after_successful17_skip_or_shrink_the_requery(self):
        for late_mask in (4, 12):
            class LateAfterNickname(VehicleConnection):
                def send(self, opcode, payload=b""):
                    super().send(opcode, payload)
                    if opcode == 16:
                        self.messages.append(reply(descriptor(1, late_mask)))

            connection = LateAfterNickname([
                (reply(descriptor(1, 0)),), (reply(descriptor(1, 8)),),
            ], fallback_names={1: "Invented"}, vehicle_zero_recovery=True)
            with self.subTest(late_mask=late_mask):
                connection.fetch_vehicles([1])
                self.assertEqual(connection.sent, {20: 2 if late_mask == 4 else 1, 16: 1})
                if late_mask == 4:
                    self.assertEqual(connection.vehicle_requests[-1], struct.pack("<BIB", 1, 1, 8))
                self.assertEqual(connection.vehicle_masks, {1: 12})
                self.assertFalse(connection.messages)

    def test_queued_uncorrelated_or_unkeyed_replies_still_stop_before_recovery(self):
        for late in (reply(descriptor(9, 0)), (17, struct.pack("<H", 48)),
                     (19, reply(descriptor(1, 4))[1] + b"x")):
            connection = VehicleConnection([
                (reply(descriptor(1, 0)), late),
            ], fallback_names={1: "Invented"}, vehicle_zero_recovery=True)
            connection.name_masks[1] = 0
            connection.fetch_nickname_fallback([1])
            with self.subTest(opcode=late[0]), self.assertRaises(ValueError):
                connection.fetch_vehicles([1])
            self.assertEqual(connection.sent, {16: 1, 20: 1})

    def test_zero_recovery_warms_once_before_its_single_extra_detail_query(self):
        for fields in (4, 12):
            connection = VehicleConnection([
                (reply(descriptor(1, 0)),), (reply(descriptor(1, fields)),),
            ], fallback_names={1: "Invented"}, vehicle_fields=fields, vehicle_zero_recovery=True)
            with self.subTest(fields=fields):
                connection.fetch_vehicles([1])
                self.assertEqual(connection.sent, {20: 2, 16: 1})
                self.assertEqual(connection.fallback_successes, {1})
                self.assertEqual(connection.vehicle_masks, {1: fields})
                self.assertEqual(connection.vehicles, {1: []})
                self.assertFalse(connection.vehicle_failures)

    def test_zero_recovery_waits_for_every_initial_identity_before_warming_or_requerying(self):
        connection = VehicleConnection([(
            reply(descriptor(1, 0), descriptor(2, 4)),
        )], fallback_names={1: "Invented"}, vehicle_zero_recovery=True)
        with self.assertRaises(TimeoutError):
            connection.fetch_vehicles([1, 2])
        self.assertEqual(connection.sent, {20: 1})
        self.assertFalse(connection.fallback_sent)
        self.assertFalse(connection.vehicle_failures)

    def test_late_queued_fields_skip_completed_and_shrink_partial_requeries_before_warmup(self):
        for fields in (4, 12):
            connection = VehicleConnection([
                (reply(descriptor(1, 0), descriptor(2, 0)),
                 reply(descriptor(1, fields), descriptor(2, 4))),
                (reply(descriptor(2, 8)),),
            ], fallback_names={1: "Invented one", 2: "Invented two"},
                vehicle_fields=fields, vehicle_zero_recovery=True)
            with self.subTest(fields=fields):
                connection.fetch_vehicles([1, 2])
                self.assertEqual(connection.sent, {20: 1} if fields == 4 else {20: 2, 16: 1})
                if fields == 12:
                    self.assertEqual(connection.vehicle_requests[-1], struct.pack("<BIB", 1, 2, 8))
                self.assertEqual(connection.vehicle_masks, {1: fields, 2: fields})
                self.assertEqual(connection.vehicles, {1: [], 2: []})

    def test_zero_after_the_single_recovery_query_times_out_without_a_third20(self):
        for initial in (reply(descriptor(1, 0)), (18, struct.pack("<BI", 1, 1))):
            connection = VehicleConnection([
                (initial,), (reply(descriptor(1, 0)),),
            ], fallback_names={1: "Invented"}, vehicle_zero_recovery=True)
            with self.subTest(first_opcode=initial[0]), self.assertRaises(TimeoutError):
                connection.fetch_vehicles([1])
            self.assertEqual(connection.sent, {20: 2, 16: 1})
            self.assertEqual(connection.vehicle_masks, {1: 0})
            self.assertEqual(connection.vehicle_failed_masks, {1: 0})
            self.assertFalse(connection.vehicle_failures)
            self.assertFalse(connection.vehicles)

    def test_zero_without_successful_warmup_never_becomes_unavailable_or_empty(self):
        for prior in ("none", "failed", "unconfirmed"):
            connection = VehicleConnection([(reply(descriptor(1, 0)),)], vehicle_zero_recovery=True)
            connection.name_masks[1] = 0
            if prior == "failed":
                connection.fetch_nickname_fallback([1])
            elif prior == "unconfirmed":
                connection.fallback_sent.add(1)
            with self.subTest(prior=prior), self.assertRaises(TimeoutError):
                connection.fetch_vehicles([1])
            self.assertEqual(connection.sent, {20: 1} if prior == "unconfirmed" else {20: 1, 16: 1})
            self.assertFalse(connection.fallback_successes)
            self.assertFalse(connection.vehicle_failures)
            self.assertFalse(connection.vehicles)
            self.assertEqual(connection.vehicle_failed_masks, {1: 0})

    def test_final_wait_validates_queued_invalid_packets_after_late_real_fields(self):
        for late in (reply(descriptor(9, 0)), (19, reply(descriptor(1, 4))[1] + b"x")):
            connection = VehicleConnection([(reply(descriptor(1, 0)),)], vehicle_zero_recovery=True)
            encoder = TransportEncoder(load_masks()["g2c"], GameDes().encrypt)
            packet = encoder.encode(*reply(descriptor(1, 12))) + encoder.encode(*late)
            # Initial zero and failed17 arrive first. The actual fields and an
            # invalid packet are decoded together only during the final wait.
            with self.subTest(late_length=len(late[1])), \
                    patch.object(connection.socket, "recv", return_value=packet) as receive:
                with self.assertRaises(ValueError):
                    connection.fetch_vehicles([1])
                receive.assert_called_once()
            self.assertEqual(connection.sent, {20: 1, 16: 1})
            self.assertEqual(connection.vehicle_masks, {1: 12})
            self.assertEqual(connection.fallback_failures, {1})
            self.assertEqual(connection.vehicle_pending, {1})
            self.assertFalse(connection.vehicle_failures)

    def test_mixed_zero18_and_real_fields_recover_once_and_preserve_late_identity_correlation(self):
        for fields in (4, 12):
            connection = VehicleConnection([
                (reply(descriptor(1, 0), descriptor(3, fields), descriptor(4, 0)),
                 (18, struct.pack("<BI", 1, 2))),
                (reply(descriptor(1, fields), descriptor(2, fields)), reply(descriptor(4, fields))),
            ], fallback_names={1: "Invented one", 2: "Invented two"},
                vehicle_fields=fields, vehicle_zero_recovery=True)
            connection.name_masks[1] = 0
            connection.fetch_nickname_fallback([1])
            with self.subTest(fields=fields):
                connection.fetch_vehicles([1, 2, 3, 4])
                self.assertEqual(connection.vehicle_requests[-1], struct.pack("<BIBIB", 2, 1, fields, 2, fields))
                self.assertEqual(connection.sent, {16: 3, 20: 2})
                self.assertEqual(connection.fallback_successes, {1, 2})
                self.assertEqual(connection.fallback_failures, {4})
                self.assertEqual(connection.vehicle_masks, {p: fields for p in (1, 2, 3, 4)})
                self.assertEqual(connection.vehicles, {p: [] for p in (1, 2, 3, 4)})
                self.assertFalse(connection.vehicle_failures)
                self.assertFalse(connection.vehicle_pending)

    def test_zero_recovery_respects_batch_bounds_and_queries_each_identity_at_most_twice(self):
        players = list(range(1, 53))
        zero_players = {p for p in players if p % 7 == 0}
        for fields in (4, 12):
            for size in (10, 25, 50):
                batches = []
                for offset in range(0, len(players), size):
                    batch = players[offset:offset + size]
                    batches.append((reply(*(descriptor(p, 0 if p in zero_players else fields) for p in batch)),))
                    retry = [p for p in batch if p in zero_players]
                    if retry:
                        batches.append((reply(*(descriptor(p, fields) for p in retry)),))
                connection = VehicleConnection(batches, fallback_names={p: "Invented" for p in zero_players},
                                               vehicle_fields=fields, vehicle_batch_size=size,
                                               vehicle_zero_recovery=True)
                connection.name_masks.update((p, 0) for p in zero_players)
                connection.fetch_nickname_fallback(sorted(zero_players))
                with self.subTest(fields=fields, batch_size=size):
                    connection.fetch_vehicles(players)
                    self.assertTrue(all(body[0] <= size for body in connection.vehicle_requests))
                    requested = [p for body in connection.vehicle_requests for p, mask in struct.iter_unpack("<IB", body[1:])]
                    self.assertEqual({p: requested.count(p) for p in players},
                                     {p: 2 if p in zero_players else 1 for p in players})
                    self.assertEqual(connection.sent[16], len(zero_players))
                    self.assertEqual(connection.vehicle_masks, {p: fields for p in players})
                    self.assertFalse(connection.vehicle_failures)

    def test_optional_zero_recovery_keeps_malformed_and_uncorrelated_guards(self):
        for packet in (reply(descriptor(9, 0)), (19, struct.pack("<BIB", 1, 1, 16)),
                       reply(descriptor(1, 4), descriptor(1, 4))):
            connection = VehicleConnection([(packet,)], vehicle_zero_recovery=True)
            with self.subTest(packet_length=len(packet[1])), self.assertRaises(ValueError):
                connection.fetch_vehicles([1])
            self.assertEqual(connection.sent, {20: 1})
            self.assertFalse(connection.fallback_sent)

    def test_zero_recovery_option_requires_bool_and_remains_disabled_by_default(self):
        self.assertFalse(VehicleConnection().vehicle_zero_recovery)
        self.assertTrue(VehicleConnection(vehicle_zero_recovery=True).vehicle_zero_recovery)
        for value in (0, 1, None, "true", 0.0, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                VehicleConnection(vehicle_zero_recovery=value)

    def test_capsule_only_mode_requests_four_and_resolves_actual_empty_and_nonempty_lists(self):
        connection = VehicleConnection([(
            reply(descriptor(1, 4, vehicles=[(8311, 3, [(1, 1, 75)])]), descriptor(2, 4)),
        )], vehicle_fields=4)
        connection.fetch_vehicles([1, 2])
        self.assertEqual(connection.vehicle_requests, [struct.pack("<BIBIB", 2, 1, 4, 2, 4)])
        self.assertEqual(connection.vehicle_masks, {1: 4, 2: 4})
        self.assertEqual(connection.vehicle_failed_masks, {1: 0, 2: 0})
        self.assertEqual(connection.vehicles[1], [
            {"capsuleTypeId": 8311, "phase": 3, "level": 75, "fate": False},
        ])
        self.assertEqual(connection.vehicles[2], [])
        self.assertFalse(connection.vehicle_failures)
        self.assertFalse(connection.vehicle_pending)
        self.assertEqual(connection.sent, {20: 1})

    def test_capsule_only_mode_recovers_an_explicit_miss_once_with_mask_four(self):
        miss = (18, struct.pack("<BI", 1, 1))
        for recovered in (False, True):
            connection = VehicleConnection([
                (miss,),
                (reply(descriptor(1, 4)),) if recovered else (miss,),
            ], fallback_names={1: "Invented"}, vehicle_fields=4)
            with self.subTest(recovered=recovered):
                connection.fetch_vehicles([1])
                self.assertEqual(connection.vehicle_requests, [struct.pack("<BIB", 1, 1, 4)] * 2)
                self.assertEqual(connection.sent, {20: 2, 16: 1})
                self.assertEqual(connection.vehicle_masks, {1: 4 if recovered else 0})
                self.assertEqual(connection.vehicle_failed_masks, {1: 0 if recovered else 4})
                self.assertEqual(connection.vehicle_failures, set() if recovered else {1})
                self.assertEqual(connection.vehicles, {1: []} if recovered else {})

    def test_capsule_only_mode_failed_nickname_keeps_the_list_unavailable(self):
        connection = VehicleConnection([((18, struct.pack("<BI", 1, 1)),)], vehicle_fields=4)
        connection.fetch_vehicles([1])
        self.assertEqual(connection.sent, {20: 1, 16: 1})
        self.assertEqual(connection.vehicle_masks, {1: 0})
        self.assertEqual(connection.vehicle_failed_masks, {1: 4})
        self.assertEqual(connection.vehicle_failures, {1})
        self.assertFalse(connection.vehicles)

    def test_capsule_only_mode_timeout_does_not_warm_or_retry(self):
        for messages in ((), (reply(descriptor(1, 0)),)):
            connection = VehicleConnection([messages], vehicle_fields=4)
            with self.subTest(replies=len(messages)), self.assertRaises(TimeoutError):
                connection.fetch_vehicles([1])
            self.assertEqual(connection.sent, {20: 1})
            self.assertEqual(connection.vehicle_masks, {1: 0})
            self.assertFalse(connection.vehicle_failures)
            self.assertFalse(connection.vehicles)

    def test_capsule_only_mode_rejects_unrequested_tactics_before_accepting_any_fields(self):
        for mask in (8, 12):
            connection = VehicleConnection([(reply(descriptor(1, mask)),)], vehicle_fields=4)
            with self.subTest(mask=mask), self.assertRaisesRegex(ValueError, "Unrequested public vehicle fields"):
                connection.fetch_vehicles([1])
            self.assertEqual(connection.sent, {20: 1})
            self.assertEqual(connection.vehicle_masks, {1: 0})
            self.assertFalse(connection.vehicles)

    def test_public_vehicle_mode_rejects_values_other_than_exact_four_or_twelve(self):
        for mode in (0, 8, 16, None, True, False, "4", 4.0, float("nan"), float("inf")):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                VehicleConnection(vehicle_fields=mode)
        self.assertEqual(VehicleConnection(vehicle_fields=4).vehicle_fields, 4)
        self.assertEqual(VehicleConnection().vehicle_fields, 12)

    def test_default_mode_still_requests_and_waits_for_capsules_and_tactics(self):
        connection = VehicleConnection([(reply(descriptor(1, 4)),)])
        with self.assertRaises(TimeoutError):
            connection.fetch_vehicles([1])
        self.assertEqual(connection.vehicle_requests, [struct.pack("<BIB", 1, 1, 12)])
        self.assertEqual(connection.vehicle_masks, {1: 4})
        self.assertFalse(connection.fallback_sent)

    def test_public_type_generation_and_carried_faculties_preserve_order(self):
        data = reply(descriptor(99, vehicles=[(8311, 3, [(1, 1, 99), (20, 1, 1)]),
                                               (9937, 2, [(1, 9, -1)])], tactics=[(123, 456)]))[1]
        self.assertEqual(detail_records(data), [(99, 12, [
            {"capsuleTypeId": 8311, "phase": 3, "level": 99, "fate": True},
            {"capsuleTypeId": 9937, "phase": 2, "level": None, "fate": False}])])

    def test_missing_level_and_zero_are_distinct(self):
        records = detail_records(reply(descriptor(99, vehicles=[(1, 0, []), (2, 1, [(1, 1, 0)])]))[1])
        self.assertEqual([v["level"] for v in records[0][2]], [None, 0])

    def test_capsule_vector_preserves_every_returned_vehicle_up_to_native_byte_count(self):
        for count in (7, 255):
            vehicles = [(i + 1, i % 4, []) for i in range(count)]
            parsed = detail_records(reply(descriptor(99, vehicles=vehicles))[1])[0][2]
            self.assertEqual([v["capsuleTypeId"] for v in parsed], list(range(1, count + 1)))

    def test_malformed_unknown_duplicate_or_trailing_data_rejected(self):
        good = reply(descriptor(99))[1]
        cases = [good[:-1], good + b"x", reply(descriptor(99), descriptor(99))[1],
                 struct.pack("<BIB", 1, 0, 0), struct.pack("<BIB", 1, 99, 0x10),
                 reply(descriptor(99, vehicles=[(1, 1, []), (1, 2, [])]))[1],
                 reply(descriptor(99, vehicles=[(1, 1, [(1, 1, 10), (1, 2, 11)])]))[1]]
        for body in cases:
            with self.subTest(length=len(body)), self.assertRaises(ValueError):
                detail_records(body)

    def test_split_masks_out_of_order_and_explicit_failure(self):
        connection = VehicleConnection([(
            reply(descriptor(2, 8, tactics=[(123, 456)])),
            reply(descriptor(1, 4, vehicles=[(8311, 3, [])])),
            (18, struct.pack("<BI", 1, 3)),
            reply(descriptor(1, 8)), reply(descriptor(2, 4)),
        )])
        connection.fetch_vehicles([1, 2, 3])
        self.assertEqual(connection.vehicle_failures, {3})
        self.assertEqual(connection.vehicle_masks, {1: 12, 2: 12, 3: 0})
        self.assertEqual(connection.vehicles[2], [])
        self.assertEqual(connection.vehicles[1][0]["capsuleTypeId"], 8311)
        self.assertFalse(connection.vehicle_pending)
        self.assertEqual(connection.sent, {20: 1, 16: 1})

    def test_disjoint_masks_for_one_player_can_share_one_reply_packet(self):
        for masks in ((4, 8), (8, 4)):
            packet = reply(*(descriptor(1, mask, vehicles=[(8311, 3, [(1, 1, 75)])],
                                        tactics=[(123, 456)]) for mask in masks))
            with self.subTest(masks=masks):
                parsed = detail_records(packet[1])
                self.assertEqual([(player, mask) for player, mask, _ in parsed],
                                 [(1, mask) for mask in masks])
                connection = VehicleConnection([(packet,)])
                connection.fetch_vehicles([1])
                self.assertEqual(connection.vehicle_masks, {1: 12})
                self.assertEqual(connection.vehicles[1], [
                    {"capsuleTypeId": 8311, "phase": 3, "level": 75, "fate": False}])
                self.assertFalse(connection.vehicle_pending)
                self.assertEqual(connection.sent, {20: 1})

    def test_overlapping_masks_for_one_player_in_one_packet_stop_without_retry(self):
        for masks in ((4, 4), (8, 8), (4, 12), (12, 8), (8, 4, 4)):
            packet = reply(*(descriptor(1, mask) for mask in masks))
            with self.subTest(masks=masks):
                with self.assertRaises(ValueError):
                    detail_records(packet[1])
                connection = VehicleConnection([(packet,)])
                with self.assertRaises(ValueError):
                    connection.fetch_vehicles([1])
                self.assertFalse(connection.vehicles)
                self.assertEqual(connection.sent, {20: 1})

    def test_zero_mask_before_or_between_fields_waits_for_the_complete_profile(self):
        for masks in ((0, 4, 8), (4, 0, 8)):
            packet = reply(*(descriptor(1, mask, vehicles=[(8311, 3, [(1, 1, 75)])])
                             for mask in masks))
            with self.subTest(masks=masks):
                parsed = detail_records(packet[1])
                self.assertEqual(next(record for record in parsed if record[1] == 0),
                                 (1, 0, None))
                connection = VehicleConnection([(packet,)])
                connection.fetch_vehicles([1])
                self.assertEqual(connection.vehicle_masks, {1: 12})
                self.assertEqual(connection.vehicles[1], [
                    {"capsuleTypeId": 8311, "phase": 3, "level": 75, "fate": False}])
                self.assertFalse(connection.vehicle_failures)
                self.assertEqual(connection.sent, {20: 1})

    def test_zero_mask_alone_times_out_without_retry_or_an_available_profile(self):
        connection = VehicleConnection([(reply(descriptor(1, 0)),)])
        with self.assertRaises(TimeoutError):
            connection.fetch_vehicles([1])
        self.assertEqual(connection.vehicle_masks, {1: 0})
        self.assertFalse(connection.vehicles)
        self.assertFalse(connection.vehicle_failures)
        self.assertFalse(connection.fallback_sent)
        self.assertEqual(connection.sent, {20: 1})

    def test_uncorrelated_zero_mask_stops_without_retry(self):
        connection = VehicleConnection([(reply(descriptor(9, 0)),)])
        with self.assertRaises(ValueError):
            connection.fetch_vehicles([1])
        self.assertEqual(connection.vehicle_masks, {1: 0})
        self.assertFalse(connection.vehicles)
        self.assertFalse(connection.vehicle_failures)
        self.assertEqual(connection.sent, {20: 1})

    def test_zero_mask_preserves_existing_observations_and_does_not_complete_fields(self):
        connection = VehicleConnection()
        connection.vehicle_pending = {1}
        connection.vehicle_masks = {1: 0}
        connection.observe(*reply(descriptor(1, 0)))
        self.assertEqual(connection.vehicle_masks, {1: 0})
        self.assertFalse(connection.vehicles)
        self.assertFalse(connection.vehicle_failures)
        connection.observe(*reply(descriptor(1, 4, vehicles=[(8311, 3, [])])))
        observed = list(connection.vehicles[1])
        connection.observe(*reply(descriptor(1, 0)))
        self.assertEqual(connection.vehicle_masks, {1: 4})
        self.assertEqual(connection.vehicles[1], observed)
        self.assertFalse(connection.vehicle_failures)
        connection.observe(*reply(descriptor(1, 8)))
        connection.observe(*reply(descriptor(1, 0)))
        self.assertEqual(connection.vehicle_masks, {1: 12})
        self.assertEqual(connection.vehicles[1], observed)

    def test_native_detail_miss_warms_once_and_requeries_after_successful17(self):
        connection = VehicleConnection([
            ((18, struct.pack("<BII", 2, 1, 2)),),
            (reply(descriptor(1, vehicles=[(8311, 3, [])])),)
        ], fallback_names={1: "Invented recovered name"})
        connection.fetch_vehicles([1, 2])
        self.assertEqual(connection.sent, {20: 2, 16: 2})
        self.assertEqual(connection.vehicle_failures, {2})
        self.assertEqual(connection.vehicles[1][0]["capsuleTypeId"], 8311)
        self.assertEqual([body[0] for body in connection.vehicle_requests], [2, 1])

    def test_partial_detail_miss_requeries_only_each_players_missing_field(self):
        connection = VehicleConnection([
            (reply(descriptor(1, 4, vehicles=[(8311, 3, [])]), descriptor(2, 8)),
             (18, struct.pack("<BII", 2, 1, 2))),
            (reply(descriptor(1, 8), descriptor(2, 4, vehicles=[])),),
        ], fallback_names={1: "Invented one", 2: "Invented two"})
        connection.fetch_vehicles([1, 2])
        self.assertEqual(connection.vehicle_requests, [
            struct.pack("<BIBIB", 2, 1, 12, 2, 12),
            struct.pack("<BIBIB", 2, 1, 8, 2, 4),
        ])
        self.assertEqual(connection.sent, {20: 2, 16: 2})
        self.assertEqual(connection.vehicle_masks, {1: 12, 2: 12})
        self.assertEqual(connection.vehicle_failed_masks, {1: 0, 2: 0})
        self.assertEqual(connection.vehicles[1][0]["capsuleTypeId"], 8311)
        self.assertEqual(connection.vehicles[2], [])
        self.assertFalse(connection.vehicle_failures)
        self.assertFalse(connection.vehicle_pending)

    def test_failed_or_prior_nickname_leaves_only_missing_capsules_unavailable(self):
        for observed in (4, 8):
            for already_queried in (False, True):
                connection = VehicleConnection([(
                    reply(descriptor(1, observed, vehicles=[(8311, 3, [])])),
                    (18, struct.pack("<BI", 1, 1)),
                )])
                if already_queried:
                    connection.fallback_sent.add(1)
                with self.subTest(observed=observed, prior_nickname=already_queried):
                    connection.fetch_vehicles([1])
                    self.assertEqual(connection.vehicle_masks, {1: observed})
                    self.assertEqual(connection.vehicle_failed_masks, {1: 12 & ~observed})
                    self.assertEqual(connection.vehicle_failures, set() if observed == 4 else {1})
                    self.assertEqual(connection.sent, {20: 1} if already_queried else {20: 1, 16: 1})
                    if observed == 4:
                        self.assertEqual(connection.vehicles[1][0]["capsuleTypeId"], 8311)
                    else:
                        self.assertNotIn(1, connection.vehicles)
                    self.assertFalse(connection.vehicle_pending)

    def test_public_export_keeps_actual_capsules_when_tactics_and_warmup_fail(self):
        class PartialVehicleSocket(InventedGameSocket):
            def reply(self, opcode, payload=b""):
                if opcode == 19 and struct.unpack_from("<I", payload, 1)[0] == 101:
                    super().reply(*reply(descriptor(101, 4, vehicles=[
                        (8311, 3, [(1, 1, 99), (20, 1, 1)]),
                    ])))
                    super().reply(18, struct.pack("<BI", 1, 101))
                else:
                    super().reply(opcode, payload)

        peer = PartialVehicleSocket(load_masks())
        with patch("collector.collect.open_socket", return_value=peer):
            result = scan_server(load_servers()[0], ("invented-account", "invented-password"), load_masks())
        self.assertTrue(result["rankingEnumerationComplete"])
        self.assertEqual(result["rows"][0]["vehiclesStatus"], "resolved")
        self.assertEqual(result["rows"][0]["vehicles"], [
            {"capsuleTypeId": 8311, "phase": 3, "level": 99, "fate": True},
        ])
        self.assertEqual(result["rows"][1]["vehiclesStatus"], "resolved")
        self.assertEqual(result["rows"][1]["vehicles"], [])
        self.assertEqual(result["rows"][2]["vehiclesStatus"], "unavailable")
        self.assertIsNone(result["rows"][2]["vehicles"])
        self.assertEqual(peer.sent[20], 1)
        self.assertEqual(peer.sent[16], 3)

    def test_second_partial_detail_miss_is_terminal_without_erasing_capsules(self):
        miss = (18, struct.pack("<BI", 1, 1))
        for observed in (4, 8):
            connection = VehicleConnection([
                (reply(descriptor(1, observed, vehicles=[(8311, 3, [])])), miss),
                (miss,),
            ], fallback_names={1: "Invented"})
            with self.subTest(observed=observed):
                connection.fetch_vehicles([1])
                self.assertEqual(connection.vehicle_requests[-1], struct.pack("<BIB", 1, 1, 12 & ~observed))
                self.assertEqual(connection.vehicle_masks, {1: observed})
                self.assertEqual(connection.vehicle_failed_masks, {1: 12 & ~observed})
                self.assertEqual(connection.vehicle_failures, set() if observed == 4 else {1})
                self.assertEqual(connection.sent, {20: 2, 16: 1})

    def test_late_nonoverlapping_fields_during_warmup_reduce_or_remove_requery(self):
        for late_mask in (4, 12):
            connection = VehicleConnection([
                ((18, struct.pack("<BI", 1, 1)),
                 reply(descriptor(1, late_mask, vehicles=[(8311, 3, [])]))),
                (reply(descriptor(1, 8)),),
            ], fallback_names={1: "Invented"})
            with self.subTest(late_mask=late_mask):
                connection.fetch_vehicles([1])
                self.assertEqual(connection.sent, {20: 2 if late_mask == 4 else 1, 16: 1})
                if late_mask == 4:
                    self.assertEqual(connection.vehicle_requests[-1], struct.pack("<BIB", 1, 1, 8))
                self.assertEqual(connection.vehicle_masks, {1: 12})
                self.assertEqual(connection.vehicle_failed_masks, {1: 0})
                self.assertFalse(connection.vehicle_failures)

    def test_requery_timeout_or_repeated_observed_field_stops_without_another_retry(self):
        for retry_replies in ((), (reply(descriptor(1, 12)),)):
            connection = VehicleConnection([
                (reply(descriptor(1, 4, vehicles=[(8311, 3, [])])),
                 (18, struct.pack("<BI", 1, 1))),
                retry_replies,
            ], fallback_names={1: "Invented"})
            with self.subTest(replies=len(retry_replies)), self.assertRaises((TimeoutError, ValueError)):
                connection.fetch_vehicles([1])
            self.assertEqual(connection.sent, {20: 2, 16: 1})
            self.assertEqual(connection.vehicle_masks, {1: 4})
            self.assertEqual(connection.vehicles[1][0]["capsuleTypeId"], 8311)

    def test_outgoing_recovery_mask_must_match_the_actual_missing_field(self):
        connection = VehicleConnection()
        connection.vehicle_pending = {1}
        connection.vehicle_masks = {1: 4}
        for mask in (0, 2, 4, 12):
            with self.subTest(mask=mask), self.assertRaises(ValueError):
                connection.send(20, struct.pack("<BIB", 1, 1, mask))
        self.assertFalse(connection.socket.writes)
        connection.send(20, struct.pack("<BIB", 1, 1, 8))
        self.assertEqual(connection.sent, {20: 1})

    def test_second_detail_miss_is_final_and_prior_nickname_is_not_repeated(self):
        miss = (18, struct.pack("<BI", 1, 1))
        connection = VehicleConnection([(miss,), (miss,)], fallback_names={1: "Invented"})
        connection.fetch_vehicles([1])
        self.assertEqual(connection.sent, {20: 2, 16: 1})
        self.assertEqual(connection.vehicle_failures, {1})
        connection = VehicleConnection([(miss,)])
        connection.fallback_sent.add(1)
        connection.fetch_vehicles([1])
        self.assertEqual(connection.sent, {20: 1})
        self.assertEqual(connection.vehicle_failures, {1})

    def test_native_batches_at_fifty_and_every_player_once(self):
        players = list(range(1, 202))
        connection = VehicleConnection([tuple(reply(descriptor(p)) for p in players[i:i + 50])
                                        for i in range(0, 200, 50)])
        connection.fetch_vehicles(players[:200])
        self.assertEqual(connection.vehicle_batch_size, 50)
        self.assertEqual([body[0] for body in connection.vehicle_requests], [50] * 4)
        self.assertEqual(len(connection.vehicles), 200)
        with self.assertRaises(ValueError):
            connection.fetch_vehicles([1])
        with self.assertRaises(ValueError):
            connection.fetch_vehicles(players)

    def test_optional_smaller_batches_preserve_each_identity_and_requested_fields(self):
        players = list(range(1, 62))
        for size in (10, 25):
            for fields in (4, 12):
                batches = [tuple(reply(descriptor(p, fields)) for p in players[i:i + size])
                           for i in range(0, len(players), size)]
                connection = VehicleConnection(batches, vehicle_fields=fields, vehicle_batch_size=size)
                with self.subTest(size=size, fields=fields):
                    connection.fetch_vehicles(players)
                    expected_counts = [min(size, len(players) - i) for i in range(0, len(players), size)]
                    self.assertEqual([body[0] for body in connection.vehicle_requests], expected_counts)
                    requested = [pair for body in connection.vehicle_requests
                                 for pair in struct.iter_unpack("<IB", body[1:])]
                    self.assertEqual(requested, [(p, fields) for p in players])
                    self.assertEqual(connection.vehicle_masks, {p: fields for p in players})
                    self.assertEqual(connection.vehicles, {p: [] for p in players})
                    self.assertEqual(connection.sent, {20: len(batches)})
                    self.assertFalse(connection.vehicle_failures)
                    self.assertFalse(connection.vehicle_pending)

    def test_optional_batch_size_requires_an_exact_integer_between_one_and_fifty(self):
        for size in (0, -1, 51, None, True, False, "10", 10.0, float("nan"), float("inf")):
            with self.subTest(size=size), self.assertRaises(ValueError):
                VehicleConnection(vehicle_batch_size=size)
        for size in (1, 50):
            self.assertEqual(VehicleConnection(vehicle_batch_size=size).vehicle_batch_size, size)

    def test_invalid_payloads_never_reach_socket(self):
        connection = VehicleConnection()
        connection.vehicle_pending = {1}
        for payload in (b"", struct.pack("<BIB", 1, 1, 4), struct.pack("<BIB", 1, 2, 12),
                        struct.pack("<BIBIB", 2, 1, 12, 1, 12), b"\x33" + b"\0" * 255):
            with self.assertRaises((ValueError, struct.error)):
                connection.send(20, payload)
        self.assertFalse(connection.socket.writes)

    def test_timeout_duplicate_or_uncorrelated_reply_stops_without_retry(self):
        cases = [(), (reply(descriptor(1, 4)),), (reply(descriptor(9)),),
                 (reply(descriptor(1, 4)), reply(descriptor(1, 4))),
                 ((18, struct.pack("<BI", 1, 9)),)]
        for messages in cases:
            connection = VehicleConnection([messages])
            with self.subTest(messages=len(messages)), self.assertRaises((TimeoutError, ValueError)):
                connection.fetch_vehicles([1])
            self.assertEqual(connection.sent, {20: 1})

    def test_snapshot_legacy_empty_missing_and_resolved_lists(self):
        server = load_servers()[0]
        value = snapshot(server)
        validate_snapshot(value, server)
        value["rows"][0].update(vehiclesStatus="resolved", vehicles=[])
        value["rows"][1].update(vehiclesStatus="unavailable", vehicles=None)
        validate_snapshot(value, server)
        value["rows"][1].update(vehiclesStatus="not_collected")
        validate_snapshot(value, server)
        value["rows"][0]["vehicles"] = [{"capsuleTypeId": 8311, "phase": 3, "level": 99, "fate": True}]
        validate_snapshot(value, server)

    def test_snapshot_rejects_private_ids_partial_contract_and_guessed_empty(self):
        server = load_servers()[0]
        for fields in ({"vehicles": []}, {"vehiclesStatus": "resolved"},
                       {"vehiclesStatus": "unavailable", "vehicles": []},
                       {"vehiclesStatus": "resolved", "vehicles": [{"instanceId": 91}]},
                       {"vehiclesStatus": "resolved", "vehicles": [{"capsuleTypeId": 1, "phase": 3,
                                                                    "level": -1, "fate": False}]}):
            value = snapshot(server)
            value["rows"][0].update(fields)
            with self.assertRaises(ValueError):
                validate_snapshot(value, server)


if __name__ == "__main__":
    unittest.main()
