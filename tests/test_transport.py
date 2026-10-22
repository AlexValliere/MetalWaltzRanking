"""Invented payload vectors generated with the reviewed Windows CNG adapter.

These are static protocol tests, not captured packets or account credentials.
The portable runtime and tests have no dependency on Windows CNG.
"""

import struct
import unittest

from collector.cipher import GameDes
from collector.configuration import load_masks, load_servers
from collector.transport import ProtocolError, TransportDecoder, TransportEncoder


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.masks, self.cipher = load_masks(), GameDes()

    def test_des_matches_cng_known_answer(self):
        key, iv = bytes.fromhex("133457799bbcdff1"), bytes(8)
        plain = bytes.fromhex("0123456789abcdef")
        encrypted = bytes.fromhex("85e813540f0ab405")
        self.assertEqual(self.cipher.encrypt(key, iv, plain), encrypted)
        self.assertEqual(self.cipher.decrypt(key, iv, encrypted), plain)

    def test_portable_outgoing_matches_cng_multiple_cursors(self):
        encoder = TransportEncoder(self.masks["c2g"], self.cipher.encrypt)
        vectors = [
            (137, struct.pack("<HIB", 5, 199, 200), "1300fa1426c97908dfbd9d589e573490eee7d3"),
            (15, struct.pack("<BIBIB", 2, 1001, 5, 1002, 5), "1300bf3cf25c1ad781ead66110d7ea4aea925c"),
            (65531, b"", "0b00c82b4012755bf5f2e4"),
        ]
        for opcode, payload, expected in vectors:
            self.assertEqual(encoder.encode(opcode, payload).hex(), expected)

    def test_incoming_cng_fragmentation_and_coalescing(self):
        wire = bytes.fromhex("2300ed44ba550ad78b19ef388bce1be9bcaa94ffd4903cd87587897c7eaa7feba94138"
                             "1b00142b6245d65a27b6ccfeac9e4b36f13ffda3e740ae3b3838b0"
                             "0b004e712fc4452931d3a5")
        expected = [(0, 131, struct.pack("<HB", 0, 2) + struct.pack("<IiIiI", 1001, 500, 1002, 499, 0xfeedbeef)),
                    (1, 380, struct.pack("<BIB", 1, 1001, 4) + "Invented 名".encode() + b"\0\0"),
                    (2, 65530, b"")]
        for chunk_size in (1, 2, 7, 19, len(wire)):
            decoder, results = TransportDecoder(self.masks["g2c"], self.cipher.decrypt), []
            for offset in range(0, len(wire), chunk_size):
                results.extend(decoder.feed(wire[offset:offset + chunk_size]))
            self.assertEqual(results, expected)
            self.assertEqual(decoder.buffer, b"")

    def test_directions_have_independent_state(self):
        outgoing = TransportEncoder(self.masks["c2g"], self.cipher.encrypt)
        incoming = TransportDecoder(self.masks["g2c"], self.cipher.decrypt)
        list(incoming.feed(bytes.fromhex("2300ed44ba550ad78b19ef388bce1be9bcaa94ffd4903cd87587897c7eaa7feba94138")))
        self.assertEqual(outgoing.encode(137, struct.pack("<HIB", 5, 199, 200)).hex(),
                         "1300fa1426c97908dfbd9d589e573490eee7d3")

    def test_invalid_framing_stops(self):
        for wire in (b"\0\0", b"\x0a\0", b"\x0c\0"):
            decoder = TransportDecoder(self.masks["g2c"], self.cipher.decrypt)
            with self.assertRaises(ProtocolError):
                list(decoder.feed(wire))

    def test_empty_payload_roundtrip_and_mask_wrap(self):
        encoder = TransportEncoder(self.masks["c2g"], self.cipher.encrypt)
        decoder = TransportDecoder(self.masks["c2g"], self.cipher.decrypt)
        for index in range(100):
            payload = bytes(range(index))
            self.assertEqual(list(decoder.feed(encoder.encode(137, payload))), [(index, 137, payload)])

    def test_shared_backend_is_fetched_once(self):
        servers = load_servers()
        self.assertEqual(len(servers), 7)
        self.assertEqual(sum(len(s["aliases"]) for s in servers), 8)
        self.assertEqual(next(s for s in servers if s["key"] == "JKMIX")["aliases"], ["JKMIX", "MIXKJ"])


if __name__ == "__main__":
    unittest.main()
