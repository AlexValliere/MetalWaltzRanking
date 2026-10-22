"""Bounded native stream reader shared by source catalogs and observed messages."""
import struct


class Reader:
    def __init__(self, data):
        self.data = data
        self.offset = 0

    def read(self, fmt):
        size = struct.calcsize("<" + fmt)
        if self.offset + size > len(self.data):
            raise ValueError("Truncated native record")
        value = struct.unpack_from("<" + fmt, self.data, self.offset)[0]
        self.offset += size
        return value

    def u8(self):
        return self.read("B")

    def u16(self):
        return self.read("H")

    def u32(self):
        return self.read("I")

    def vector(self, fmt="I"):
        return [self.read(fmt) for _ in range(self.u8())]

    def string(self, maximum=65536):
        end = self.data.find(b"\0", self.offset, self.offset + maximum + 1)
        if end < 0:
            raise ValueError("Invalid native string")
        value = self.data[self.offset:end].decode("utf-8", errors="strict")
        self.offset = end + 1
        return value

    def take(self, size):
        if type(size) is not int or not 0 <= size <= len(self.data) - self.offset:
            raise ValueError("Truncated native bytes")
        result = self.data[self.offset:self.offset + size]
        self.offset += size
        return result

    def finish(self):
        if self.offset != len(self.data):
            raise ValueError("Unexpected bytes after native records")
