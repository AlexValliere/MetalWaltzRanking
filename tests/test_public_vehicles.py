import socket
import struct
import unittest

from collector.records import detail_records
from collector.snapshots import validate_snapshot
from test_collection import snapshot
from test_connection import Clock, SyntheticSocket
from collector.configuration import load_masks, load_servers
from collector.connection import CollectorConnection
from collector.cipher import GameDes


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
    def __init__(self, batches=(), fallback_names=None):
        self.fake_clock = Clock()
        super().__init__(SyntheticSocket(self.fake_clock), load_masks(), GameDes(), b"invented-login",
                         clock=self.fake_clock.monotonic, sleeper=self.fake_clock.sleep)
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
    def test_public_type_generation_and_carried_faculties_preserve_order(self):
        data = reply(descriptor(99, vehicles=[(8311, 3, [(1, 1, 99), (20, 1, 1)]),
                                               (9937, 2, [(1, 9, -1)])], tactics=[(123, 456)]))[1]
        self.assertEqual(detail_records(data), [(99, 12, [
            {"capsuleTypeId": 8311, "phase": 3, "level": 99, "fate": True},
            {"capsuleTypeId": 9937, "phase": 2, "level": None, "fate": False}])])

    def test_missing_level_and_zero_are_distinct(self):
        records = detail_records(reply(descriptor(99, vehicles=[(1, 0, []), (2, 1, [(1, 1, 0)])]))[1])
        self.assertEqual([v["level"] for v in records[0][2]], [None, 0])

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
        self.assertEqual([body[0] for body in connection.vehicle_requests], [50] * 4)
        self.assertEqual(len(connection.vehicles), 200)
        with self.assertRaises(ValueError):
            connection.fetch_vehicles([1])
        with self.assertRaises(ValueError):
            connection.fetch_vehicles(players)

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
                 ((18, struct.pack("<BI", 1, 9)),),
                 (reply(descriptor(1, 4)), (18, struct.pack("<BI", 1, 1)))]
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
