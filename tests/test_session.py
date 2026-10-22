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
from collector.records import ServerRejection
from collector.transport import TransportDecoder, TransportEncoder


class InventedGameSocket:
    def __init__(self, masks, *, login_status=0, commanders=1):
        cipher = GameDes()
        self.decoder = TransportDecoder(masks["c2g"], cipher.decrypt)
        self.encoder = TransportEncoder(masks["g2c"], cipher.encrypt)
        self.pending, self.sent = bytearray(), Counter()
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
    def test_complete_read_only_workflow_with_names_and_unavailable_name(self):
        masks = load_masks()
        peer = InventedGameSocket(masks)
        with patch("collector.collect.open_socket", return_value=peer):
            result = scan_server(load_servers()[0], ("invented-account", "invented-password"), masks)
        self.assertTrue(peer.closed)
        self.assertTrue(result["rankingEnumerationComplete"])
        self.assertFalse(result["namesComplete"])
        self.assertEqual(result["rows"], [
            {"rank": 1, "name": "Invented 名", "grandeur": 900, "nameStatus": "resolved"},
            {"rank": 2, "name": "Invented B", "grandeur": 800, "nameStatus": "resolved"},
            {"rank": 3, "name": None, "grandeur": 700, "nameStatus": "unavailable"}])
        self.assertEqual(peer.sent, {324: 1, 396: 1, 316: 1, 357: 1, 374: 1, 436: 1,
                                     137: 2, 15: 1, 16: 2, 65531: 2})

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
