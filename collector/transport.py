"""Framing and independent cipher cursors; no sockets, routes, or game commands.

Adapted from MaidenWaltz bridge/maiden_waltz/protocol/transport.py, itself
adapted from MMD TransportDecoder and TransportEncoder. Encryption is injected.
Incoming and outgoing streams always use separate decoder and encoder cursors.
"""

import struct


class ProtocolError(ValueError):
    pass


class TransportDecoder:
    def __init__(self, mask, decrypt):
        if not isinstance(mask, bytes) or len(mask) < 16:
            raise ProtocolError("Invalid transport mask")
        self.mask, self.decrypt = mask, decrypt
        self.buffer = bytearray()
        self.xor_position = self.des_position = self.message_index = 0

    def feed(self, data):
        self.buffer.extend(data)
        while len(self.buffer) >= 2:
            length = int.from_bytes(self.buffer[:2], "little")
            if length < 11 or length % 8 != 3:
                raise ProtocolError("Invalid message framing; a fresh connection is required")
            if len(self.buffer) < length:
                return
            encrypted = self.buffer[2:length]
            del self.buffer[:length]
            clear = bytes(
                byte ^ self.mask[(self.xor_position + i) % len(self.mask)]
                for i, byte in enumerate(encrypted)
            )
            self.xor_position = (self.xor_position + len(encrypted)) % len(self.mask)
            padding = clear[0]
            if padding > 7:
                raise ProtocolError("Invalid transport padding")
            material = bytes(
                self.mask[(self.des_position + i) % len(self.mask)] for i in range(16)
            )
            self.des_position = (self.des_position + 16) % len(self.mask)
            plaintext = self.decrypt(material[8:], material[:8], clear[1:])
            if len(plaintext) != len(clear) - 1:
                raise ProtocolError("Cipher returned an invalid block length")
            if padding:
                plaintext = plaintext[:-padding]
            if len(plaintext) < 2:
                raise ProtocolError("Missing message opcode")
            opcode = int.from_bytes(plaintext[:2], "little")
            index = self.message_index
            self.message_index += 1
            yield index, opcode, plaintext[2:]


class TransportEncoder:
    def __init__(self, mask, encrypt):
        if not isinstance(mask, bytes) or len(mask) < 16:
            raise ProtocolError("Invalid transport mask")
        self.mask, self.encrypt = mask, encrypt
        self.xor_position = self.des_position = self.message_index = 0

    def encode(self, opcode, payload):
        if type(opcode) is not int or not 0 <= opcode <= 65535 or not isinstance(payload, bytes):
            raise ProtocolError("Invalid outgoing message")
        plain = struct.pack("<H", opcode) + payload
        padding = -len(plain) % 8
        if len(plain) + padding + 3 > 65535:
            raise ProtocolError("Outgoing message exceeds framing limit")
        material = bytes(self.mask[(self.des_position + i) % len(self.mask)] for i in range(16))
        encrypted = self.encrypt(material[8:], material[:8], plain + bytes(padding))
        if len(encrypted) != len(plain) + padding:
            raise ProtocolError("Cipher returned an invalid block length")
        clear = bytes([padding]) + encrypted
        masked = bytes(
            byte ^ self.mask[(self.xor_position + i) % len(self.mask)]
            for i, byte in enumerate(clear)
        )
        result = struct.pack("<H", len(masked) + 2) + masked
        self.xor_position = (self.xor_position + len(masked)) % len(self.mask)
        self.des_position = (self.des_position + 16) % len(self.mask)
        self.message_index += 1
        return result
