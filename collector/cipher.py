"""Portable implementation of the native game's legacy DES-CBC blocks.

The native framing, key/IV order and zero padding are retained. This cipher is
for interoperability with the existing game service, not new encryption.
"""

from Crypto.Cipher import DES


class GameDes:
    @staticmethod
    def crypt(key, iv, data, *, decrypt=False):
        if len(key) != 8 or len(iv) != 8 or not data or len(data) % 8:
            raise ValueError("Invalid cipher block")
        cipher = DES.new(key, DES.MODE_CBC, iv=iv)
        return cipher.decrypt(data) if decrypt else cipher.encrypt(data)

    def encrypt(self, key, iv, data):
        return self.crypt(key, iv, data)

    def decrypt(self, key, iv, data):
        return self.crypt(key, iv, data, decrypt=True)
