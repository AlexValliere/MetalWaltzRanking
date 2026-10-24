"""Full login/entry/ranking/name workflow against an invented in-memory peer."""

from collections import Counter
import socket
import struct
import unittest
from unittest.mock import patch

from collector.binary import Reader
from collector.cipher import GameDes
from collector.collect import CollectionStopped, scan_server
from collector.configuration import load_masks, load_servers
from collector.connection import CollectorConnection
from collector.records import ServerRejection
from collector.transport import TransportDecoder, TransportEncoder


class InventedGameSocket:
    def __init__(self, masks, *, login_status=0, commanders=1):
        cipher = GameDes()
        self.decoder = TransportDecoder(masks["c2g"], cipher.decrypt)
        self.encoder = TransportEncoder(masks["g2c"], cipher.encrypt)
        self.pending, self.sent = bytearray(), Counter()
        self.rank_requests = []
        self.login_status, self.commanders, self.closed = login_status, commanders, False
        self.ranks = [(101, 900), (102, 800), (103, 700)]

    def reply(self, opcode, payload=b""):
        self.pending.extend(self.encoder.encode(opcode, payload))

    def sendall(self, data):
        for _, opcode, payload in self.decoder.feed(data):
            self.sent[opcode] += 1
            if opcode == 324:
                body = struct.pack("<H", self.login_status)
                if not self.login_status:
                    body += struct.pack("<I", 55) + b"invented-account\0" + struct.pack("<BI", 0, 0)
                self.reply(331, body)
            elif opcode == 396:
                body = struct.pack("<HB", 0, self.commanders)
                if self.commanders:
                    # Verified reconnect player lists may carry an empty name.
                    body += struct.pack("<I", 91) + b"\0" + struct.pack("<BI", 0, 0)
                self.reply(310, body)
            elif opcode == 316:
                self.reply(362, b"\0" + struct.pack("<BII", 0, 0, 0))
                self.reply(312, struct.pack("<HII", 0, 55, 91))
            elif opcode == 357:
                self.reply(368)
            elif opcode == 374:
                self.reply(371, struct.pack("<HH", 0, 0))
            elif opcode == 436:
                self.reply(434, b"\0")
                self.reply(438)
            elif opcode == 137:
                fund, offset, count = struct.unpack("<HIB", payload)
                assert fund == 5
                self.rank_requests.append((offset, count))
                page = self.ranks[offset:offset + count]
                self.reply(131, struct.pack("<HB", 0, len(page)) +
                           b"".join(struct.pack("<Ii", player, score) for player, score in page) + struct.pack("<I", 999))
            elif opcode == 15:
                r = Reader(payload)
                for _ in range(r.u8()):
                    player, mask = r.u32(), r.u8()
                    assert mask == 5
                    if player == 101:
                        self.reply(380, struct.pack("<BIB", 1, player, 4) + "Invented 名".encode() + b"\0\0")
                    else:
                        self.reply(14, struct.pack("<BI", 1, player))
                r.finish()
            elif opcode == 16:
                player, = struct.unpack("<I", payload)
                self.reply(65530)  # A verified ACK may interleave a fallback.
                if player == 102:
                    self.reply(17, struct.pack("<HI", 0, player) + b"Invented B\0\0")
                else:
                    self.reply(17, struct.pack("<H", 48))
            elif opcode == 20:
                r = Reader(payload)
                for _ in range(r.u8()):
                    player, mask = r.u32(), r.u8()
                    assert mask == 0x0c
                    if player == 101:
                        # A carried public level, one Fate Pin and one tactic.
                        self.reply(19, struct.pack("<BIBBIBB", 1, player, 0x0c, 1, 8311, 3, 2)
                                   + struct.pack("<BHiBHi", 1, 1, 99, 20, 1, 1)
                                   + struct.pack("<BII", 1, 999, 123))
                    elif player == 102:
                        # A real empty public list is distinct from lookup failure.
                        self.reply(19, struct.pack("<BIBBB", 1, player, 0x0c, 0, 0))
                    else:
                        self.reply(18, struct.pack("<BI", 1, player))
                r.finish()
            elif opcode != 65531:
                raise AssertionError("Unexpected request in read-only session")

    def recv(self, size):
        if not self.pending:
            raise socket.timeout()
        fragment = bytes(self.pending[:17])
        del self.pending[:17]
        return fragment

    def settimeout(self, seconds):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True


class SessionTests(unittest.TestCase):
    def test_repeated_keyed_zero_publishes_unresolved_without_losing_other_profiles(self):
        class PersistentZeroSocket(InventedGameSocket):
            def reply(self, opcode, payload=b""):
                if opcode == 19 and struct.unpack_from("<I", payload, 1)[0] == 102:
                    payload = struct.pack("<BIB", 1, 102, 0)
                super().reply(opcode, payload)

        masks = load_masks()
        peer = PersistentZeroSocket(masks)
        with patch("collector.collect.open_socket", return_value=peer):
            result = scan_server(load_servers()[0], ("invented-account", "invented-password"), masks)
        self.assertTrue(peer.closed)
        self.assertTrue(result["rankingEnumerationComplete"])
        self.assertEqual(peer.rank_requests, [(0, 200), (3, 200)])
        self.assertEqual(peer.sent[20], 2)
        self.assertEqual(peer.sent[16], 2)
        self.assertEqual([row["vehiclesStatus"] for row in result["rows"]],
                         ["resolved", "unresolved", "unavailable"])
        self.assertTrue(result["rows"][0]["vehicles"])
        self.assertIsNone(result["rows"][1]["vehicles"])
        self.assertIsNone(result["rows"][2]["vehicles"])

    def test_final_ranking_boundary_drains_late_vehicle_fields_with_normal_validation(self):
        class LateVehicleSocket(InventedGameSocket):
            def __init__(self, masks, late_identity):
                super().__init__(masks)
                self.late_identity = late_identity

            def reply(self, opcode, payload=b""):
                if opcode == 19 and struct.unpack_from("<I", payload, 1)[0] == 102:
                    payload = struct.pack("<BIB", 1, 102, 0)
                super().reply(opcode, payload)
                if opcode == 131 and payload[:3] == b"\0\0\0":
                    # The late real empty list is already decoded behind the
                    # empty-ranking boundary; it must still be validated.
                    super().reply(19, struct.pack("<BIBBB", 1, self.late_identity, 12, 0, 0))

            def recv(self, size):
                if not self.pending:
                    raise socket.timeout()
                data = bytes(self.pending)
                self.pending.clear()
                return data

        masks = load_masks()
        peer = LateVehicleSocket(masks, 102)
        with patch("collector.collect.open_socket", return_value=peer):
            result = scan_server(load_servers()[0], ("invented-account", "invented-password"), masks)
        self.assertEqual(result["rows"][1]["vehiclesStatus"], "resolved")
        self.assertEqual(result["rows"][1]["vehicles"], [])
        self.assertEqual(peer.sent[20], 2)
        peer = LateVehicleSocket(masks, 999999)
        with patch("collector.collect.open_socket", return_value=peer), self.assertRaises(ValueError):
            scan_server(load_servers()[0], ("invented-account", "invented-password"), masks)
        self.assertTrue(peer.closed)

    def test_mobile_zero_detail_reply_recovers_only_missing_identity_after_prior_successful17(self):
        class ZeroThenVehicleSocket(InventedGameSocket):
            def __init__(self, masks):
                super().__init__(masks)
                self.detail_counts = Counter()

            def reply(self, opcode, payload=b""):
                if opcode == 19:
                    player = struct.unpack_from("<I", payload, 1)[0]
                    self.detail_counts[player] += 1
                    if player == 102:
                        if self.detail_counts[player] == 1:
                            payload = struct.pack("<BIB", 1, player, 0)
                        else:
                            payload = (struct.pack("<BIBBIBB", 1, player, 12, 1, 9937, 2, 1)
                                       + struct.pack("<BHiB", 1, 1, 77, 0))
                super().reply(opcode, payload)

        masks = load_masks()
        peer = ZeroThenVehicleSocket(masks)
        with patch("collector.collect.open_socket", return_value=peer):
            result = scan_server(load_servers()[0], ("invented-account", "invented-password"), masks)
        self.assertTrue(peer.closed)
        self.assertTrue(result["rankingEnumerationComplete"])
        self.assertEqual(peer.sent[20], 2)
        self.assertEqual(peer.sent[16], 2)  # Existing name lookups only; no repeated16.
        self.assertEqual(peer.detail_counts, {101: 1, 102: 2})
        self.assertEqual(result["rows"][1]["vehiclesStatus"], "resolved")
        self.assertEqual(result["rows"][1]["vehicles"], [
            {"capsuleTypeId": 9937, "phase": 2, "level": 77, "fate": False},
        ])
        self.assertEqual(result["rows"][2]["vehiclesStatus"], "unavailable")
        self.assertIsNone(result["rows"][2]["vehicles"])

    def test_production_steam_connection_keeps_zero_recovery_and_vehicle_collection_disabled(self):
        server = next(server for server in load_servers() if server["key"] == "SteamBonn")
        peer = InventedGameSocket(load_masks("steam"))
        with patch("collector.collect.open_socket", return_value=peer), \
                patch("collector.collect.CollectorConnection", wraps=CollectorConnection) as constructor:
            result = scan_server(server, ("invented-account", "invented-password"), load_masks())
        self.assertIs(constructor.call_args.kwargs["vehicle_zero_recovery"], False)
        self.assertIs(constructor.call_args.kwargs["vehicle_partial_results"], False)
        self.assertEqual(peer.sent[20], 0)
        self.assertTrue(all(row["vehiclesStatus"] == "not_collected" and row["vehicles"] is None
                            for row in result["rows"]))

    def test_complete_read_only_workflow_with_names_and_unavailable_name(self):
        masks = load_masks()
        peer = InventedGameSocket(masks)
        with patch("collector.collect.open_socket", return_value=peer):
            result = scan_server(load_servers()[0], ("invented-account", "invented-password"), masks)
        self.assertTrue(peer.closed)
        self.assertTrue(result["rankingEnumerationComplete"])
        self.assertFalse(result["namesComplete"])
        self.assertEqual(result["rows"], [
            {"rank": 1, "name": "Invented 名", "grandeur": 900, "nameStatus": "resolved",
             "vehiclesStatus": "resolved", "vehicles": [{"capsuleTypeId": 8311, "phase": 3, "level": 99, "fate": True}]},
            {"rank": 2, "name": "Invented B", "grandeur": 800, "nameStatus": "resolved",
             "vehiclesStatus": "resolved", "vehicles": []},
            {"rank": 3, "name": None, "grandeur": 700, "nameStatus": "unavailable",
             "vehiclesStatus": "unavailable", "vehicles": None}])
        self.assertEqual(peer.sent, {324: 1, 396: 1, 316: 1, 357: 1, 374: 1, 436: 1,
                                     137: 2, 15: 1, 16: 2, 20: 1, 65531: 2})

    def test_login_rejection_stops_before_any_followup(self):
        masks = load_masks()
        peer = InventedGameSocket(masks, login_status=12)
        with patch("collector.collect.open_socket", return_value=peer):
            with self.assertRaises(ServerRejection) as caught:
                scan_server(load_servers()[0], ("invented-account", "invented-password"), masks)
        self.assertEqual((caught.exception.phase, caught.exception.status), ("login", 12))
        self.assertEqual(peer.sent, {324: 1})
        self.assertTrue(peer.closed)

    def test_missing_commander_stops_without_creation(self):
        masks = load_masks()
        peer = InventedGameSocket(masks, commanders=0)
        with patch("collector.collect.open_socket", return_value=peer):
            with self.assertRaises(CollectionStopped) as caught:
                scan_server(load_servers()[0], ("invented-account", "invented-password"), masks)
        self.assertEqual(caught.exception.code, "commander_missing")
        self.assertEqual(peer.sent, {324: 1, 396: 1})
        self.assertTrue(peer.closed)


if __name__ == "__main__":
    unittest.main()
