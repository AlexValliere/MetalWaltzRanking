"""Synthetic vector computed by the pinned Steam binary's DES implementation.

Ghidra P-code emulation ran only the native key schedule and CBC functions in
an isolated memory overlay. This vector contains no account or captured data.
See docs/research/steam-transport-evidence.json for source fingerprints.
"""

import unittest

from collector.cipher import GameDes
from collector.configuration import load_masks
from collector.transport import TransportEncoder


class SteamNativeCipherTests(unittest.TestCase):
    def test_nonzero_iv_matches_emulated_steam_native_cipher(self):
        cipher = GameDes()
        key = bytes.fromhex("133457799bbcdff1")
        iv = bytes.fromhex("0102030405060708")
        plain = bytes.fromhex("0123456789abcdef")
        encrypted = bytes.fromhex("e61690cc695580f6")
        self.assertEqual(cipher.encrypt(key, iv, plain), encrypted)
        self.assertEqual(cipher.decrypt(key, iv, encrypted), plain)

    def test_steam_frames_match_native_cbc_with_separate_cursors(self):
        cipher = GameDes()
        encoder = TransportEncoder(load_masks("steam")["c2g"], cipher.encrypt)
        vectors = [
            (137, "0500c7000000c8", "13007ed25b8aa9e26059304375318be4315ebf"),
            (15, "02e903000005ea03000005", "130056c4e2c64c8a950fd365ee8ea41175c8b0"),
            (65531, "", "0b006ae3f7c66d83226f48"),
        ]
        for opcode, payload_hex, expected_hex in vectors:
            self.assertEqual(encoder.encode(opcode, bytes.fromhex(payload_hex)).hex(), expected_hex)

    def test_steam_native_vectors_cross_both_mask_cursor_boundaries(self):
        cipher = GameDes()
        encoder = TransportEncoder(load_masks("steam")["c2g"], cipher.encrypt)
        payload = bytes.fromhex("0500c7000000c8")
        # A 17-byte masked body crosses the XOR boundary at frame 36. The
        # independent 16-byte key/IV sampler crosses its boundary at frame 38.
        expected = {
            36: "1300cfb7bd865de98cea20d44c7a3658a1f8fa",
            38: "130023f76e9e96d42986d72b7afb2a217e3da5",
            39: "1300eff9d352a1cae9d5527ce4625dc7a65fd5",
        }
        for frame in range(40):
            encoded = encoder.encode(137, payload)
            if frame in expected:
                with self.subTest(frame=frame):
                    self.assertEqual(encoded.hex(), expected[frame])


if __name__ == "__main__":
    unittest.main()
